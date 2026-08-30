import pytest
from mk2 import user_profile


def test_user_profile_crud(tmp_path, monkeypatch):
    test_path = tmp_path / "user_profile.json"
    monkeypatch.setattr(user_profile, "USER_PROFILE_PATH", test_path)

    prof = user_profile.get_user_profile()
    assert isinstance(prof, dict)
    assert "basics" in prof
    assert "depth_score" in prof

    prof["basics"]["name"] = "Tony Stark"
    prof["projects"].append({"name": "Mark 85", "status": "active"})
    user_profile.save_user_profile(prof)

    loaded = user_profile.get_user_profile()
    assert loaded["basics"]["name"] == "Tony Stark"
    assert len(loaded["projects"]) == 1
    assert loaded["depth_score"] > 10

    prompt = user_profile.format_profile_prompt()
    assert "Tony Stark" in prompt
    assert "Mark 85" in prompt
