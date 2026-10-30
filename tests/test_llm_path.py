"""Exercise the real-provider code path of the gateway with a scripted fake chat model."""

from types import SimpleNamespace

from app.agents.graph import run_agent
from app.agents.state import RoutingDecision
from app.config import Settings
from app.llm import gateway as gw_module


class FakeChatModel:
    """Duck-typed LangChain chat model: structured output, tool calling and text."""

    def __init__(self, log):
        self.log = log

    def with_structured_output(self, schema):
        log = self.log

        class Structured:
            def invoke(self, messages):
                log.append("structured")
                # The "LLM" picks the wrong site on purpose: sanitization must keep it valid.
                return schema(intent="advice", agents=["energy_advisor", "consumption_analyst"],
                              site_id="PLANT-KEN", days=14, reasoning="LLM plan")

        return Structured()

    def bind_tools(self, tools):
        log = self.log
        assert {t.name for t in tools} >= {"get_consumption_summary", "compare_periods"}

        class Bound:
            def invoke(self, messages):
                log.append("tools")
                return SimpleNamespace(tool_calls=[
                    {"name": "compare_periods", "args": {"site_id": "PLANT-KEN", "days": 14}},
                    {"name": "get_consumption_summary", "args": {"site_id": "PLANT-KEN", "days": 14}},
                ])

        return Bound()

    def invoke(self, messages):
        self.log.append("write")
        return SimpleNamespace(content=[{"type": "text", "text": "LLM text with key sk-abcdefghijklmnop12"}],
                               usage_metadata={"input_tokens": 100, "output_tokens": 20})


def test_real_provider_path(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(gw_module, "_build_chat_model", lambda settings: FakeChatModel(log))
    gw_module.reset_gateway(Settings(llm_provider="groq", groq_api_key="x", llm_max_retries=0))

    r = run_agent("How can we save energy at the Kenitra plant?")

    assert r["llm"]["provider"] == "groq"
    assert r["plan"] == ["consumption_analyst", "energy_advisor"]  # LLM plan sanitized & ordered
    assert r["days"] == 14 and r["plan_reasoning"] == "LLM plan"
    assert set(r["analysis"]) == {"compare_periods", "get_consumption_summary"}  # tools chosen by the LLM
    assert "structured" in log and "tools" in log and log.count("write") >= 3
    assert "sk-abcdefghijklmnop12" not in r["answer"]  # output guardrail redacts credentials
    assert r["status"] == "ok"
    gw_module.reset_gateway()


def test_structured_output_failure_falls_back_to_rules(monkeypatch):
    class Broken(FakeChatModel):
        def with_structured_output(self, schema):
            class S:
                def invoke(self, messages):
                    return RoutingDecision.model_validate({"intent": "nonsense"})  # raises ValidationError
            return S()

    monkeypatch.setattr(gw_module, "_build_chat_model", lambda settings: Broken([]))
    gw_module.reset_gateway(Settings(llm_provider="openai", openai_api_key="x", llm_max_retries=0))
    r = run_agent("Any anomalies at the clinic?")
    assert r["intent"] == "anomaly" and r["site_id"] == "CLINIC-MRK"
    sup = next(t for t in r["trace"] if t["agent"] == "supervisor")
    assert sup["status"] == "degraded"
    gw_module.reset_gateway()
