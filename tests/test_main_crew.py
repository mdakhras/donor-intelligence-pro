import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add the backend directory to the sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../backend')))

from main import run_donor_intel_crew
from models import ResearchData, DonorProfile, Strategy, Guidance, ReportDraft, FinalReport

class TestRunDonorIntelCrew(unittest.TestCase):

    def setUp(self):
        self.original_cwd = os.getcwd()
        # Change the CWD to 'backend' so that the config files can be found
        os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), '../backend')))

    def tearDown(self):
        os.chdir(self.original_cwd)

    @patch('main.search_donor_articles')
    @patch('main._build_llm')
    def test_run_donor_intel_crew_e2e(self, mock_build_llm, mock_search_donor_articles):
        # Arrange
        # Mock the LLM to return predictable outputs for each agent
        mock_llm = MagicMock()

        # This is a simplified approach. A real test would have the mock LLM
        # return different, structured responses based on the agent's prompt.
        mock_llm.invoke.return_value = '{"report": "This is the final report."}'

        mock_build_llm.return_value = mock_llm

        # Mock the web search to return a predictable result
        mock_search_donor_articles.return_value = [
            {"title": "Test Article", "body": "This is a test article.", "url": "http://example.com"}
        ]

        # Mock Task.execute_sync for each task
        with patch('crewai.Task.execute_sync') as mock_execute:
            mock_execute.side_effect = [
                ResearchData(findings=["Finding 1"]),
                DonorProfile(profile="Test Profile"),
                Strategy(recommendation="Test Strategy"),
                Guidance(guidance="Test Guidance"),
                ReportDraft(draft="Test Draft"),
                FinalReport(report="This is the final report.")
            ]

            # Act
            result = run_donor_intel_crew(
                donor_name="Test Donor",
                canonical_donor_name="Test Donor",
                region="Test Region",
                theme="Test Theme",
                user_role="Test Role",
                existing_profile="Existing profile info.",
                recent_activity="Recent activity info.",
                document_content="Document content."
            )

            # Assert
            self.assertIn("final_report", result)
            self.assertEqual(result["final_report"], "This is the final report.")
            self.assertEqual(mock_execute.call_count, 6)

if __name__ == '__main__':
    unittest.main()
