def test_imports():
    """Test that we can import the main modules."""
    try:
        from app.main import app
        from app.config import settings
        from app.db import engine
        print("[OK] Main imports successful")
        return True
    except Exception as e:
        print(f"[ERROR] Import error: {e}")
        return False

def test_models():
    """Test that we can import the models."""
    try:
        from app.models.tables import User, Agent, Provider
        from app.models.agent_config import AgentConfig
        print("[OK] Model imports successful")
        return True
    except Exception as e:
        print(f"[ERROR] Model import error: {e}")
        return False

if __name__ == "__main__":
    success1 = test_imports()
    success2 = test_models()
    if success1 and success2:
        print("[OK] All structure tests passed!")
    else:
        print("[ERROR] Some tests failed!")
        exit(1)