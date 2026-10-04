import pytest

from config import load_workspaces


def workspace_config(policy):
    return {
        "workspaces": {
            "neutral-workspace": {
                "path": ".",
                **policy,
            }
        }
    }


def test_workspace_v1_hash_policy_accepts_explicit_boolean():
    workspaces = load_workspaces(workspace_config({
        "protocol_v1_require_prompt_sha256": True,
    }))

    assert workspaces["neutral-workspace"][
        "protocol_v1_require_prompt_sha256"
    ] is True


@pytest.mark.parametrize("policy", [{}, {
    "protocol_v1_require_prompt_sha256": "true",
}])
def test_workspace_v1_hash_policy_fails_closed_when_absent_or_malformed(policy):
    with pytest.raises(ValueError, match="must declare boolean"):
        load_workspaces(workspace_config(policy))
