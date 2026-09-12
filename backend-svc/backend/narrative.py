"""Azure OpenAI (gpt-4o) gap explanations + remediation suggestions."""
from backend import azure_openai


def explain_gap(control: dict, evidence: str, state: str) -> str:
    """Return a 1-2 sentence justification + one remediation step for a Partial/Gap."""
    prompt = (
        "You are a compliance analyst. A document was assessed against this control:\n"
        f"Framework: {control['framework']} | Control {control['control_id']}: {control['title']}\n"
        f"Control text: {control['text']}\n\n"
        f"Best matching evidence from the document:\n\"{evidence}\"\n\n"
        f"Assessed state: {state}.\n"
        "In 1-2 sentences, explain the gap and give one concrete remediation step."
    )
    return azure_openai.chat(prompt, max_tokens=250)
