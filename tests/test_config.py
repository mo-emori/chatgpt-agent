import unittest

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


class WorkspaceV1HashPolicyTests(unittest.TestCase):
    def test_accepts_explicit_boolean(self):
        workspaces = load_workspaces(workspace_config({
            "protocol_v1_require_prompt_sha256": True,
        }))
        self.assertIs(workspaces["neutral-workspace"][
            "protocol_v1_require_prompt_sha256"], True)

    def test_fails_closed_when_absent(self):
        with self.assertRaisesRegex(ValueError, "must declare boolean"):
            load_workspaces(workspace_config({}))

    def test_fails_closed_when_malformed(self):
        with self.assertRaisesRegex(ValueError, "must declare boolean"):
            load_workspaces(workspace_config({
                "protocol_v1_require_prompt_sha256": "true",
            }))


if __name__ == "__main__":
    unittest.main()
