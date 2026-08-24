import os

from app.main import create_controller


def test_demo_provider_selection():
    os.environ["RETAIL_ASSIST_MODE"] = "DEMO"

    controller = create_controller()

    assert controller is not None
    assert controller.rag_service is not None
    assert controller.rag_service.retriever.__class__.__name__ == "DemoRetriever"
    assert controller.rag_service.generator.__class__.__name__ == "DemoGenerator"


def test_invalid_provider_mode():
    os.environ["RETAIL_ASSIST_MODE"] = "INVALID"

    try:
        create_controller()
        assert False, "Expected ValueError"
    except ValueError as error:
        assert "DEMO" in str(error)
        assert "SNOWFLAKE" in str(error)

    os.environ["RETAIL_ASSIST_MODE"] = "DEMO"
