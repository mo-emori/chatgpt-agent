import json, tempfile, unittest
from pathlib import Path
from review_decision import parse_block, validate, ReviewDecisionError
from historical_review_decision import adopt_historical_review_decision

def decision(**changes):
    value={"schema":"normalized-review-decision","schema_version":1,"review_job_id":"R1",
      "actor":"claude","workspace":"argus","capability":"CAP","review_mode":"FULL_REVIEW",
      "verdict":"CHANGES_REQUESTED","findings":[{"finding_id":"F-1","severity":"MODERATE",
      "status":"OPEN","summary":"x","affected_paths":["src/a.py"],"authority_refs":[],
      "evidence_refs":[],"trust_class":"ACTOR_REPORTED"}],"provenance":{},
      "trust_class":"ACTOR_REPORTED"}
    value.update(changes); return value

class ReviewDecisionTests(unittest.TestCase):
    def test_valid_block_and_actor_trust(self):
        value,error=parse_block("x<REVIEW_DECISION>"+json.dumps(decision())+"</REVIEW_DECISION>",
          review_job_id="R1",workspace="argus",capability="CAP")
        self.assertIsNone(error); self.assertEqual(value["findings"][0]["trust_class"],"ACTOR_REPORTED")

    def test_missing_and_malformed_are_nonfatal(self):
        self.assertEqual(parse_block("ordinary prose"),(None,None))
        value,error=parse_block("<REVIEW_DECISION>{bad}</REVIEW_DECISION>")
        self.assertIsNone(value); self.assertTrue(error)

    def test_duplicate_enum_identity_and_paths_rejected(self):
        variants=[decision(findings=decision()["findings"]*2), decision(verdict="PASS"),
          decision(review_job_id="WRONG"), decision(workspace="other"), decision(capability="OTHER"),
          decision(findings=[{**decision()["findings"][0],"affected_paths":["../escape"]}])]
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(ReviewDecisionError):
                    validate(value,review_job_id="R1",workspace="argus",capability="CAP")

    def test_historical_requires_approval_and_exact_id_provenance(self):
        with tempfile.TemporaryDirectory(dir=".tmp-tests") as td:
            root=Path(td); log=root/"log"; log.mkdir(); mapping=root/"mapping.json"
            (log/"review-execution.json").write_text(json.dumps({"exit_code":0,"final_result_text":"Finding F-1"}))
            mapping.write_text(json.dumps(decision(capability=None)))
            failed=adopt_historical_review_decision(canonical=root,evidence_root="evidence",source_job_id="R1",
              workspace="argus",log_dir=log,mapping_path=mapping,human_approved=False)
            self.assertEqual(failed["status"],"FAILED")
            ok=adopt_historical_review_decision(canonical=root,evidence_root="evidence",source_job_id="R1",
              workspace="argus",log_dir=log,mapping_path=mapping,human_approved=True)
            self.assertEqual(ok["status"],"ADOPTED")
            adopted=json.loads((root/"evidence/R1/review-decision.json").read_text())
            self.assertEqual(adopted["trust_class"],"ACTOR_REPORTED")
            self.assertEqual(adopted["provenance"]["normalization_mode"],"HISTORICAL_MANUAL")

if __name__ == "__main__": unittest.main()
