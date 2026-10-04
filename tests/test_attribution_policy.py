import unittest

from attribution_policy import attribution_paths


class GeneratedInfrastructureAttributionPolicyTests(unittest.TestCase):
    def test_nested_generated_artifacts_are_excluded(self):
        paths = [
            "build/context-bundles/FOO-ANALYZER/FOO-JOB-1/post-actor/job-context.json",
            "build/context-bundles/FOO-ANALYZER/evidence-index.json",
        ]
        self.assertEqual(attribution_paths(
            paths,
            generated_roots=["build/context-bundles/FOO-ANALYZER"],
            protected_paths=["engine/classify.foo", "context/analyzer-guide.md"]), [])

    def test_real_target_and_authority_remain_attributable(self):
        generated = "build/context-bundles/FOO-ANALYZER/FOO-JOB-1/report.json"
        self.assertEqual(attribution_paths(
            [generated, "engine/classify.foo", "context/analyzer-guide.md"],
            generated_roots=["build/context-bundles/FOO-ANALYZER"],
            protected_paths=["engine/classify.foo", "context/analyzer-guide.md"]),
            ["context/analyzer-guide.md", "engine/classify.foo"])

    def test_generated_root_overlap_with_target_or_source_fails_closed(self):
        for protected in ("build/context-bundles/FOO-ANALYZER/result.foo",
                          "build/context-bundles/**/authority.md"):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                attribution_paths(
                    ["build/context-bundles/FOO-ANALYZER/result.foo"],
                    generated_roots=["build/context-bundles/FOO-ANALYZER"],
                    protected_paths=[protected])

    def test_validation_directory_is_not_globally_ignored(self):
        self.assertEqual(attribution_paths(
            ["validation/user-target.txt"],
            generated_roots=["validation/context/CAP"],
            protected_paths=["validation/user-target.txt"]),
            ["validation/user-target.txt"])


if __name__ == "__main__":
    unittest.main()
