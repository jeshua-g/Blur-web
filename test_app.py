from app import blur_cfg, classify_render, safe_name


def test_safe_name_strips_paths():
    assert safe_name("../../etc/passwd.mp4") == "passwd.mp4"
    assert safe_name("") == "video.mp4"


def test_cfg_interpolate_off():
    text = blur_cfg({"blur_amount": 1.0, "interpolate": False})
    assert "interpolate: false" in text
    assert "preview: false" in text
    assert "blur amount: 1.0" in text


def test_cfg_clamps_and_picks():
    text = blur_cfg({"blur_amount": 99, "interpolate": True, "blur_weighting": "nope", "encode_preset": "h265"})
    assert "blur amount: 4.0" in text
    assert "interpolate: true" in text
    assert "blur weighting: equal" in text
    assert "encode preset: h265" in text


def test_classify_render():
    code, msg = classify_render("Video 'x' is not a valid video or is unreadable", 1)
    assert code == "unreadable_video"
    code, msg = classify_render("RIFE error: no vulkan device", 1)
    assert code == "rife_failed"
    code, msg = classify_render("something else", 7)
    assert code == "render_failed"
    assert "7" in msg


if __name__ == "__main__":
    test_safe_name_strips_paths()
    test_cfg_interpolate_off()
    test_cfg_clamps_and_picks()
    test_classify_render()
    print("ok")
