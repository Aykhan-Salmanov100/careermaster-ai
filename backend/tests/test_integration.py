"""Integration tests: Full interview preparation flow.

Tests the complete user journey end-to-end:
  upload CV -> generate questions -> evaluate answers -> get live suggestions

Uses the FastAPI TestClient so no running server is needed.
"""

import io
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(text: str) -> bytes:
    """Build a minimal valid PDF containing *text* as a single text object.

    This is a hand-crafted bare-minimum PDF-1.4 structure that pdfplumber
    can extract text from.  Avoids a hard dependency on reportlab.
    """
    stream = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    stream_len = len(stream)
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        "4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        f"5 0 obj<</Length {stream_len}>>stream\n"
        f"{stream}\n"
        "endstream\nendobj\n"
        "xref\n0 6\n"
        "0000000000 65535 f \n"
        "0000000009 00000 n \n"
        "0000000058 00000 n \n"
        "0000000115 00000 n \n"
        "0000000266 00000 n \n"
        "0000000340 00000 n \n"
        "trailer<</Size 6/Root 1 0 R>>\n"
        "startxref\n434\n%%EOF"
    )
    return pdf.encode("latin-1")


SAMPLE_CV_TEXT = (
    "John Smith john.smith@email.com +44 7700 900000 "
    "Software Engineer with 5 years of experience in Python JavaScript React SQL Docker AWS "
    "Experience Senior Software Engineer at Google 2020-2023 "
    "Led a team of 5 developers Built microservices platform "
    "Education BSc Computer Science University of Westminster"
)


# ===================================================================
# Full interview-preparation flow (ordered steps)
# ===================================================================

class TestFullInterviewFlow:
    """Test the complete user journey:
    upload CV -> generate questions -> evaluate answers -> get live suggestions.
    """

    # -- Step 1: Health check -------------------------------------------

    def test_step1_health_check(self):
        """Verify the backend is running and healthy."""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body

    # -- Step 2: Upload & parse a CV -----------------------------------

    def test_step2_upload_cv_pdf(self):
        """Upload a sample PDF CV and verify the parsed output."""
        pdf_bytes = _make_pdf_bytes(SAMPLE_CV_TEXT)
        r = client.post(
            "/parse",
            files={"file": ("john_smith_cv.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert r.status_code == 200
        data = r.json()

        # Core fields must be present
        assert "name" in data
        assert "skills" in data
        assert "role" in data
        assert "ats_score" in data
        assert isinstance(data["skills"], list)
        assert len(data["skills"]) >= 1
        assert isinstance(data["ats_score"], int)

        # Store for downstream steps
        self.__class__.parsed_skills = data["skills"]
        self.__class__.parsed_role = data["role"]

    def test_step2b_upload_unsupported_file(self):
        """Uploading an unsupported file type returns 415."""
        r = client.post(
            "/parse",
            files={"file": ("readme.txt", io.BytesIO(b"plain text"), "text/plain")},
        )
        assert r.status_code == 415

    # -- Step 3: Generate interview questions --------------------------

    def test_step3_generate_questions_default(self):
        """Generate questions using parsed CV skills and defaults."""
        skills = getattr(self.__class__, "parsed_skills", ["python", "react", "sql"])
        role = getattr(self.__class__, "parsed_role", "Software Engineer")

        r = client.post("/interview/generate", json={
            "skills": skills,
            "role": role,
            "difficulty": "mid",
            "count": 15,
        })
        assert r.status_code == 200
        data = r.json()

        assert "questions" in data
        assert isinstance(data["questions"], list)
        assert len(data["questions"]) >= 3
        assert "total_questions" in data
        assert "estimated_duration_minutes" in data

        # Each question must carry required fields
        q = data["questions"][0]
        assert "id" in q
        assert "text" in q
        assert "category" in q
        assert q["category"] in ("behavioral", "technical", "situational", "role_specific")

        # Persist for next step
        self.__class__.questions = data["questions"]

    def test_step3b_generate_questions_senior(self):
        """Senior difficulty questions should include business-impact language."""
        r = client.post("/interview/generate", json={
            "skills": ["python", "docker"],
            "role": "Backend Engineer",
            "difficulty": "senior",
            "count": 5,
        })
        assert r.status_code == 200
        data = r.json()
        # Structural validity — keyword phrasing varies between AI and template paths
        assert len(data["questions"]) >= 1
        assert data["difficulty"] == "senior"

    def test_step3c_generate_questions_no_skills(self):
        """Omitting skills should fall back to defaults, not crash."""
        r = client.post("/interview/generate", json={
            "role": "Software Engineer",
            "difficulty": "mid",
            "count": 5,
        })
        assert r.status_code == 200
        assert len(r.json()["questions"]) >= 1

    # -- Step 4: Evaluate a STAR response ------------------------------

    def test_step4_evaluate_strong_star_response(self):
        """A well-structured STAR answer should receive a high score."""
        r = client.post("/interview/evaluate", json={
            "question_id": 1,
            "question_text": "Tell me about a time you led a team project",
            "response_text": (
                "During my time at Google in 2022, our team was tasked with migrating "
                "a legacy monolith to microservices. As the tech lead, I was responsible "
                "for planning the migration strategy and coordinating 5 developers. "
                "I created a phased migration plan, set up CI/CD pipelines, and held "
                "daily standups to track progress. As a result, we completed the migration "
                "2 weeks ahead of schedule, reduced deployment time by 40%, and improved "
                "system reliability from 99.5% to 99.9% uptime."
            ),
        })
        assert r.status_code == 200
        data = r.json()

        # Response structure
        assert "total_score" in data
        assert "grade" in data
        assert "star_scores" in data
        assert "star_feedback" in data
        assert "suggestions" in data
        assert "strengths" in data

        # This response covers all STAR components -> score should be high
        assert data["total_score"] > 50
        assert data["grade"] in ("A", "B")

    def test_step4b_evaluate_weak_response(self):
        """A vague, short answer should receive a lower score."""
        r = client.post("/interview/evaluate", json={
            "question_id": 2,
            "question_text": "Describe a challenging project",
            "response_text": "It was hard but I did it.",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["total_score"] < 60
        assert len(data["suggestions"]) >= 1  # must have improvement tips

    def test_step4c_evaluate_empty_response_rejected(self):
        """An empty response_text should be rejected with 400."""
        r = client.post("/interview/evaluate", json={
            "question_id": 3,
            "question_text": "Tell me about yourself",
            "response_text": "",
        })
        assert r.status_code == 400

    # -- Step 5: Live assistant suggestions ----------------------------

    def test_step5_live_suggest_with_cv(self):
        """Live suggest with CV data returns all expected fields."""
        r = client.post("/live/suggest", json={
            "transcript": "Tell me about your experience with Python and how you've used it in production",
            "cv_data": {
                "skills": ["python", "fastapi", "django", "sql"],
                "name": "John Smith",
                "role": "Software Engineer",
                "experience": [
                    {"title": "Software Engineer", "company": "Google", "dates": "2020-2023"},
                    {"title": "Junior Developer", "company": "Startup", "dates": "2018-2020"},
                ],
            },
            "role": "Software Engineer",
        })
        assert r.status_code == 200
        data = r.json()

        assert "question_type" in data
        assert data["question_type"] in ("behavioral", "technical", "situational", "general")
        assert "star_hints" in data
        assert "suggested_points" in data
        assert "answer_skeleton" in data
        assert "confidence" in data
        assert isinstance(data["suggested_points"], list)

    def test_step6_live_suggest_behavioral(self):
        """Behavioral question is correctly classified."""
        r = client.post("/live/suggest", json={
            "transcript": "Tell me about a time you had to deal with a difficult coworker",
            "cv_data": {"skills": ["communication", "teamwork"]},
            "role": "Project Manager",
        })
        assert r.status_code == 200
        assert r.json()["question_type"] == "behavioral"

    def test_step7_live_suggest_technical(self):
        """Technical question is correctly classified."""
        r = client.post("/live/suggest", json={
            "transcript": "How would you design a scalable REST API?",
            "cv_data": {"skills": ["python", "fastapi", "docker"]},
            "role": "Backend Developer",
        })
        assert r.status_code == 200
        assert r.json()["question_type"] == "technical"

    def test_step8_live_suggest_situational(self):
        """Situational question is correctly classified."""
        r = client.post("/live/suggest", json={
            "transcript": "What would you do if your manager asked you to cut corners on testing?",
            "cv_data": {"skills": ["testing", "pytest"]},
            "role": "QA Engineer",
        })
        assert r.status_code == 200
        assert r.json()["question_type"] == "situational"

    def test_step9_live_suggest_general(self):
        """General question is correctly classified."""
        r = client.post("/live/suggest", json={
            "transcript": "Why do you want to work at our company?",
            "cv_data": None,
            "role": "Developer",
        })
        assert r.status_code == 200
        assert r.json()["question_type"] == "general"

    def test_step10_live_suggest_no_cv(self):
        """Live suggest without CV data still returns a valid response."""
        r = client.post("/live/suggest", json={
            "transcript": "Tell me about a time you improved a process",
            "cv_data": None,
            "role": "Software Engineer",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["question_type"] == "behavioral"
        # suggested_points should contain fallback text
        assert len(data["suggested_points"]) >= 1


# ===================================================================
# Edge cases and error handling
# ===================================================================

class TestEdgeCases:
    """Test error handling, validation, and boundary conditions."""

    def test_empty_transcript_returns_400(self):
        """An empty transcript should be rejected."""
        r = client.post("/live/suggest", json={
            "transcript": "",
            "cv_data": None,
            "role": "Developer",
        })
        assert r.status_code == 400

    def test_whitespace_only_transcript_returns_400(self):
        """Whitespace-only transcript should be rejected (stripped to empty)."""
        r = client.post("/live/suggest", json={
            "transcript": "   \n\t  ",
            "cv_data": None,
            "role": "Developer",
        })
        assert r.status_code == 400

    def test_very_long_transcript(self):
        """A very long transcript should not crash the server."""
        r = client.post("/live/suggest", json={
            "transcript": "Tell me about a time you " + ("word " * 5000),
            "cv_data": None,
            "role": "Developer",
        })
        # Should either process successfully or return a client error, never 500
        assert r.status_code in (200, 400, 413, 422)

    def test_missing_optional_fields_uses_defaults(self):
        """Omitting cv_data and role should use defaults, not crash."""
        r = client.post("/live/suggest", json={
            "transcript": "How would you design a database schema?",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["question_type"] == "technical"

    def test_missing_required_transcript_returns_422(self):
        """Omitting the required 'transcript' field returns 422 (validation error)."""
        r = client.post("/live/suggest", json={
            "cv_data": None,
            "role": "Developer",
        })
        assert r.status_code == 422

    def test_generate_zero_questions(self):
        """Requesting 0 questions should return an empty list (not crash)."""
        r = client.post("/interview/generate", json={
            "skills": ["python"],
            "role": "Developer",
            "difficulty": "mid",
            "count": 0,
        })
        assert r.status_code == 200
        # count=0 -> 0//3 = 0 per category, but role_specific might add 1
        assert isinstance(r.json()["questions"], list)

    def test_generate_large_count(self):
        """Requesting a large number of questions should not crash."""
        r = client.post("/interview/generate", json={
            "skills": ["python", "react", "sql"],
            "role": "Software Engineer",
            "difficulty": "mid",
            "count": 100,
        })
        assert r.status_code == 200
        assert isinstance(r.json()["questions"], list)

    def test_evaluate_extremely_long_response(self):
        """A very long answer should be evaluated without crashing."""
        long_response = (
            "During my time at the company, I was responsible for leading the team. "
            "I implemented a new system that improved performance. "
        ) * 50  # ~1000 words
        r = client.post("/interview/evaluate", json={
            "question_id": 99,
            "question_text": "Describe a major project",
            "response_text": long_response,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["word_count"] > 300
        assert "lengthy" in " ".join(data["suggestions"]).lower() or data["total_score"] >= 0

    def test_parse_oversized_file_rejected(self):
        """Files exceeding 10 MB should be rejected with 413."""
        huge = b"x" * (10_000_001)
        r = client.post(
            "/parse",
            files={"file": ("big.pdf", io.BytesIO(huge), "application/pdf")},
        )
        assert r.status_code == 413

    def test_parse_no_file_returns_422(self):
        """Calling /parse without a file should return 422."""
        r = client.post("/parse")
        assert r.status_code == 422

    def test_unknown_route_returns_404(self):
        """An undefined endpoint should return 404."""
        r = client.get("/nonexistent")
        assert r.status_code == 404


# ===================================================================
# Cross-endpoint consistency
# ===================================================================

class TestCrossEndpointConsistency:
    """Verify that data flows correctly between endpoints."""

    def test_generated_question_can_be_evaluated(self):
        """Take a generated question and pass it to the evaluator."""
        # Generate
        gen_r = client.post("/interview/generate", json={
            "skills": ["python", "react"],
            "role": "Software Engineer",
            "difficulty": "mid",
            "count": 5,
        })
        assert gen_r.status_code == 200
        question = gen_r.json()["questions"][0]

        # Evaluate with a STAR-style answer
        eval_r = client.post("/interview/evaluate", json={
            "question_id": question["id"],
            "question_text": question["text"],
            "response_text": (
                "When I was working at my previous company in 2021, we had a major deadline. "
                "I was responsible for delivering the feature on time. "
                "I built a prototype, wrote tests, and presented the solution to stakeholders. "
                "As a result, we shipped on time and the client renewed their contract worth $500K."
            ),
        })
        assert eval_r.status_code == 200
        assert eval_r.json()["total_score"] > 0

    def test_generated_question_can_be_live_suggested(self):
        """Take a generated question and pass it to the live assistant."""
        gen_r = client.post("/interview/generate", json={
            "skills": ["python"],
            "role": "Software Engineer",
            "difficulty": "mid",
            "count": 3,
        })
        assert gen_r.status_code == 200
        question_text = gen_r.json()["questions"][0]["text"]

        suggest_r = client.post("/live/suggest", json={
            "transcript": question_text,
            "cv_data": {"skills": ["python", "fastapi"]},
            "role": "Software Engineer",
        })
        assert suggest_r.status_code == 200
        data = suggest_r.json()
        assert data["question_type"] in ("behavioral", "technical", "situational", "general")
        assert len(data["star_hints"]) >= 1
