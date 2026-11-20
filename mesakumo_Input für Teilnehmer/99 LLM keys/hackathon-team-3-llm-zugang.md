# Hackathon – LLM-Zugang Team 3

## Euer Zugang

| | |
|---|---|
| **API-Key** | `rqsty-sk-1/nr5fYMTsOL0R5VKYiEo6pvDjFrzUybLMxIMCUU7rnSZ20hgQ/ZMQo2uC2/1blwei+W3oBZ/ZV7AjVvhkdMa/n1+FLVe9IhfGhPTTGsnAg=` |
| **Endpunkt (Base URL)** | `https://router.eu.requesty.ai/v1` |
| **Budget** | max. **40 USD** an Tokens für diesen Key |
| **Gültig bis** | **Sonntag, 22.11.2026** – danach wird der Key deaktiviert |

Der Key gehört nur eurem Team. Nicht weitergeben, nicht in Git committen, nicht in Screenshots zeigen.

## Systeme

Der Zugang läuft über **Requesty**, ein LLM-Gateway mit OpenAI-kompatibler Schnittstelle. Verarbeitet wird in der EU (AWS Bedrock, Region Irland).

Freigeschaltet sind genau diese zwei Modelle:

| Modell-Name (so im Code angeben) | Wofür |
|---|---|
| `bedrock/claude-sonnet-5@eu-west-1` | Claude Sonnet 5 – stark, für anspruchsvolle Aufgaben |
| `bedrock/claude-haiku-4-5@eu-west-1` | Claude Haiku 4.5 – schnell und günstig, für einfache oder viele Aufrufe |

Andere Modelle werden abgelehnt.

## Anleitung

### 1. Key als Umgebungsvariable setzen

```bash
export REQUESTY_API_KEY="rqsty-sk-1/nr5fYMTsOL0R5VKYiEo6pvDjFrzUybLMxIMCUU7rnSZ20hgQ/ZMQo2uC2/1blwei+W3oBZ/ZV7AjVvhkdMa/n1+FLVe9IhfGhPTTGsnAg="
```

### 2a. Test mit curl

```bash
curl https://router.eu.requesty.ai/v1/chat/completions \
  -H "Authorization: Bearer $REQUESTY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "bedrock/claude-haiku-4-5@eu-west-1", "max_tokens": 50, "messages": [{"role": "user", "content": "Sag Hallo"}]}'
```

### 2b. Python (OpenAI-SDK)

```bash
pip install openai
```

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://router.eu.requesty.ai/v1",
    api_key=os.environ["REQUESTY_API_KEY"],
)

antwort = client.chat.completions.create(
    model="bedrock/claude-sonnet-5@eu-west-1",
    max_tokens=500,
    messages=[{"role": "user", "content": "Erkläre in zwei Sätzen, was ein Hackathon ist."}],
)
print(antwort.choices[0].message.content)
```

### 2c. JavaScript / TypeScript (OpenAI-SDK)

```bash
npm install openai
```

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://router.eu.requesty.ai/v1",
  apiKey: process.env.REQUESTY_API_KEY,
});

const antwort = await client.chat.completions.create({
  model: "bedrock/claude-haiku-4-5@eu-west-1",
  max_tokens: 500,
  messages: [{ role: "user", content: "Sag Hallo" }],
});
console.log(antwort.choices[0].message.content);
```

Andere Tools, die eine „OpenAI-kompatible“ Schnittstelle unterstützen, funktionieren genauso: Base URL und Key eintragen, Modellnamen von oben verwenden.

## Tipps

- **Budget schonen:** Zum Ausprobieren Haiku nehmen, Sonnet für das Ergebnis. Immer `max_tokens` setzen.
- **Fehler 401:** Key falsch kopiert oder bereits deaktiviert.
- **Fehler 403:** Modellname falsch geschrieben oder nicht freigeschaltet.
- **Fehler 429 / Budget erreicht:** Die 40 USD sind aufgebraucht – bitte bei der Orga melden.
