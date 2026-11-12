import os
import json
import httpx

AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_ENDPOINT = os.getenv("AI_ENDPOINT", "")
AI_MODEL = os.getenv("AI_MODEL", "Smart-Route")
# Deprecated DeepSeek variables kept for fallback
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """
You are HyperHeal AIOps Agent, an autonomous infrastructure reliability AI.
You diagnose Linux server/VM anomalies based on an Evidence Pack (telemetry metrics, logs, top processes).

Analyze the incident and output a STRICT JSON response adhering to this format:
{
  "root_cause": "A concise explanation of the root cause in Bahasa Indonesia",
  "risk_level": "low" | "medium" | "high",
  "explanation_id": "Detailed diagnostic explanation in Bahasa Indonesia",
  "remediation_script": "A single-line or chain of safe bash commands to mitigate the spike",
  "rollback_script": "Bash command to restore service if mitigation fails"
}

Safety Rules:
- Never generate destructive commands like `rm -rf /`, `mkfs`, or disk wiping commands.
- Focus on graceful remediation (e.g., reloading overloaded services, killing hanging runaway workers, dropping cache, renicing processes).
- Return ONLY valid JSON, no markdown code blocks.
"""

async def analyze_incident(evidence_pack: dict) -> dict:
    api_key = AI_API_KEY or DEEPSEEK_API_KEY
    endpoint = f"{AI_ENDPOINT.rstrip('/')}/chat/completions" if AI_ENDPOINT else DEEPSEEK_API_URL
    model = AI_MODEL or "Smart-Route"

    if not api_key or api_key == "YOUR_DEEPSEEK_API_KEY_HERE":
        return {
            "root_cause": "[Mock / Fallback] Spike resource terdeteksi pada target VM.",
            "risk_level": "low",
            "explanation_id": "API Key belum diatur. Menggunakan rekomendasi heuristik standar.",
            "remediation_script": "sync && echo 3 > /proc/sys/vm/drop_caches",
            "rollback_script": "true"
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": f"Here is the incident Evidence Pack:\n{json.dumps(evidence_pack, indent=2)}"}
        ],
        "temperature": 0.2
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(endpoint, headers=headers, json=payload)
        res.raise_for_status()
        data = res.json()
        content = data["choices"][0]["message"]["content"].strip()
        
        # Clean markdown code blocks if returned
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        return json.loads(content)
