"""Two servers that fetch the same file into the shared cache at the same moment both get it."""

import threading

from agentic_sim_world_gen import atomic
from agentic_sim_world_gen.assets import remote_layers

N = 150


def test_write_is_whole_and_leaves_nothing_behind(tmp_path):
    p = tmp_path / "a" / "b.json"
    atomic.write(p, "一")
    atomic.write(p, b"two")
    assert p.read_bytes() == b"two" and [f.name for f in p.parent.iterdir()] == ["b.json"]
    assert atomic.temp_for(p) != atomic.temp_for(p) and atomic.temp_for(p).parent == p.parent


def test_two_fetchers_of_the_same_layers(tmp_path):
    """Both pass the "not in the cache yet" test before either writes (the barrier), as two processes do."""
    gate = threading.Barrier(2, timeout=10)

    class Reply:
        content = b"PK" + b"x" * 4096

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, timeout=None):
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            return Reply()

    errors, got = [], []

    def run():
        f = remote_layers._Fetcher(tmp_path)  # one per server
        f.session = lambda: Session()
        try:
            got.append([f.local_path(f"https://example.com/assets/m{i}.usdz") for i in range(N)])
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            gate.abort()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert got[0] == got[1] and None not in got[0]
    files = sorted(f.name for f in (tmp_path / "example.com" / "assets").iterdir())
    assert files == sorted(f"m{i}.usdz" for i in range(N))  # no temporary file left
    assert all((tmp_path / "example.com" / "assets" / n).read_bytes() == Reply.content for n in files)


def test_layer_is_replaced_whole_while_another_reads_it(tmp_path):
    """A fetched layer is rewritten (remote references -> local files) while another server may be opening it."""
    from pxr import Sdf

    for ext, magic in ((".usdc", b"PXR-USDC"), (".usda", b"#usda")):
        p = tmp_path / f"a{ext}"
        layer = Sdf.Layer.CreateAnonymous(ext)
        Sdf.CreatePrimInLayer(layer, "/A").customData = {"n": 0, "pad": "x" * 200_000}
        atomic.export_layer(layer, p)
        assert p.read_bytes().startswith(magic)  # the format follows the extension, not the temporary name
        opened = Sdf.Layer.FindOrOpen(str(p))
        stop, bad = threading.Event(), []

        def read():
            while not stop.is_set():
                try:
                    got = Sdf.Layer.OpenAsAnonymous(str(p))
                    if got is None or "n" not in got.GetPrimAtPath("/A").customData:
                        bad.append("partial")
                except Exception as e:  # noqa: BLE001
                    bad.append(e)

        t = threading.Thread(target=read)
        t.start()
        for n in range(1, 40):
            layer.GetPrimAtPath("/A").customData["n"] = n
            atomic.export_layer(layer, p)
        stop.set()
        t.join()
        assert bad == []
        assert opened.GetPrimAtPath("/A").customData["n"] == 39  # the layer this process had open follows the file
        assert [f.name for f in tmp_path.iterdir() if f.name.startswith(".")] == []
