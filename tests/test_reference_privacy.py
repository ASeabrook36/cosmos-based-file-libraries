from scripts.check_reference_privacy import findings


def test_private_values_are_reported_without_echoing_them():
    result = findings(b'line one\nendpoint="private-demo-account"\n', ["private-demo-account"])
    assert result == [(2, "private environment reference")]
    assert "private-demo-account" not in str(result)


def test_user_home_paths_are_rejected_but_portable_placeholders_are_allowed():
    # Assemble fixture paths so the privacy checker does not flag its own test source.
    for prefix in (b"/Users/", b"/home/", b"C:\\Users\\"):
        separator = b"\\" if prefix.startswith(b"C:") else b"/"
        assert findings(prefix + b"example-person" + separator + b"project", [])
    assert not findings(b"~/project ${HOME}/project /Users/<USER>/project /home/<USER>/project", [])
