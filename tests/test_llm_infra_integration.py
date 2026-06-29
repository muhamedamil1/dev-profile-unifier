def test_phase7f_and_phase9_share_same_gemini_client():
    from app.dependencies import get_gemini_client, get_gemini_ambiguity_reviewer, get_summary_service

    shared = get_gemini_client()
    summary_service = get_summary_service()
    ambiguity_reviewer = get_gemini_ambiguity_reviewer()

    assert getattr(summary_service, "gemini_client") is shared
    assert getattr(ambiguity_reviewer, "gemini_client") is shared
