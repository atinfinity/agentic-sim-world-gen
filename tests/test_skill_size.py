"""The video instructions an agent reads first stay small, and they keep the measuring steps."""
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "world-gen"


def test_videos_md_small_and_measures():
    v = (SKILL / "videos.md").read_text(encoding="utf-8")
    assert len(v.encode()) < 9000  # was 16 KB; the details are in videos-fit.md
    head = v[:1500]
    for step in ("video_poses", "fit_photos", "compare_photo"):
        assert step in head
    assert "videos-fit.md" in v


def test_links_resolve():
    import re

    for f in SKILL.glob("*.md"):
        for link in re.findall(r"\]\(([a-z-]+\.md)\)", f.read_text(encoding="utf-8")):
            assert (SKILL / link).exists(), (f.name, link)
