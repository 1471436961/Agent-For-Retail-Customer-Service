"""Evidence views never pretend a text reply proves explanation or consent."""
import unittest
from support_agent.state import SessionState, record_reply
from support_agent.progress import evidence_view, unresolved_operations
from tau2.data_model.message import AssistantMessage

class ProgressTests(unittest.TestCase):
    def test_arbitrary_reply_records_only_chronology(self):
        p={"proposal_id":"draft","state":"prepared","facts":{"kind":"exchange","order_id":"owned"}}
        s=SessionState(verified_customer_id="verified",customer_profile_loaded=True,user_turn=1,
                       proposals={"draft":p},quote_response_pending=p)
        record_reply(s,AssistantMessage(role="assistant",content="I am checking a policy; no quote has been explained."),{})
        view=evidence_view(s)
        self.assertTrue(view["quotes"][0]["assistant_response_after_quote"])
        self.assertEqual(set(view["quotes"][0]),{"id","kind","order_id","state","assistant_response_after_quote"})
        self.assertTrue(unresolved_operations(s))
        self.assertNotIn("stage",view)
        self.assertEqual(s.proposals["draft"]["state"],"prepared")

    def test_write_outcomes_do_not_infer_all_goals_complete(self):
        for outcome in ("prepared","attempted","pending","unknown","accepted","completed","failed"):
            with self.subTest(outcome=outcome):
                s=SessionState(submissions={"ref":{"kind":"return","state":outcome,"order_id":"owned"}})
                self.assertEqual(unresolved_operations(s),outcome in {"prepared","attempted","pending","unknown"})
                self.assertEqual(evidence_view(s)["actual_submissions"][0]["state"],outcome)
                self.assertNotIn("complete",evidence_view(s))
