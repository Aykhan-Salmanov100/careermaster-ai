import pytest
from fastapi.testclient import TestClient
from main import app
import os
import tempfile

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def sample_pdf_bytes():
    """Create a minimal valid PDF for testing."""
    # Minimal PDF that contains text "John Smith Software Engineer Python Java"
    # Using reportlab or a hardcoded minimal PDF
    content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n5 0 obj<</Length 44>>stream\nBT /F1 12 Tf 100 700 Td (John Smith Software Engineer Python Java SQL React experience at Google 2020-2023 BSc Computer Science University of Westminster) Tj ET\nendstream\nendobj\nxref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000266 00000 n \n0000000340 00000 n \ntrailer<</Size 6/Root 1 0 R>>\nstartxref\n434\n%%EOF"
    return content

@pytest.fixture
def sample_cv_text():
    return """
    John Smith
    john.smith@email.com | +44 7700 900000

    Software Engineer with 5 years of experience in Python, JavaScript, and React.

    Experience:
    Senior Software Engineer at Google (2020-2023)
    - Led a team of 5 developers to build a microservices platform
    - Increased deployment speed by 40% through CI/CD pipeline optimization
    - Implemented automated testing reducing bugs by 60%

    Junior Developer at Startup Inc (2018-2020)
    - Developed REST APIs using Python and FastAPI
    - Built responsive frontend components with React

    Education:
    BSc Computer Science, University of Westminster (2014-2018)

    Skills: Python, JavaScript, React, SQL, Docker, AWS, Git, FastAPI
    """
