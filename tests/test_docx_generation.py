import os
from utils.document_generator import generate_docx

def test_generate_docx_creates_file(tmp_path):
    # Setup
    sample_text = "Overview:\nThis is a test.\nKey Players:\n- Entity A\n- Entity B"
    file_path = tmp_path / "test_output.docx"

    # Act
    generate_docx(sample_text, str(file_path))

    # Assert
    assert file_path.exists()
    assert file_path.stat().st_size > 0
