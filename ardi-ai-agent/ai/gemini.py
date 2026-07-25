import json
import logging
import asyncio
import time
import random
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)
client = genai.Client(api_key=GEMINI_API_KEY)

MODELS = [
    "gemini-3.5-flash-lite",
]

MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds, doubles each attempt

TONES = {
    "friendly": "Be warm, casual, and chatty — like a helpful friend. Use simple everyday language.",
    "professional": "Be polished, courteous, and well-spoken — like a high-end store associate. Keep it clean and respectful.",
    "casual": "Be relaxed and chill — like texting a buddy. Short responses, slang is fine.",
    "formal": "Be proper and dignified — like a classic boutique. Full sentences, very polite.",
    "witty": "Be playful and clever — light humor, fun energy. Keep it enjoyable but still helpful.",
}

# ─── Ardi AI Identity (shared mechanism for all interactions) ──────────────

ARD_IDENTITY = """You are Ardi AI, an Ethiopian sales automation assistant built by Ardi Technologies.
You are Ardi — not Google Gemini, not an AI model, not a chatbot. You are Ardi.
Never mention Gemini, Google, OpenAI, artificial intelligence, or any technical terminology.
Respond in the user's language — Amharic, English, or mixed — whatever they use.
Be helpful, accurate, and direct."""


def _call_model_sync(contents, model_index=0, attempt=0, system_instruction=None):
    if model_index >= len(MODELS):
        raise RuntimeError("All Gemini models exhausted")
    model = MODELS[model_index]
    try:
        kwargs = {"model": model, "contents": contents}
        if system_instruction:
            kwargs["config"] = types.GenerateContentConfig(
                system_instruction=system_instruction
            )
        response = client.models.generate_content(**kwargs)
        text = response.text
        if text is None:
            logger.warning("Model %s returned None text, candidates=%s", model, getattr(response, 'candidates', None))
            raise ValueError("Model returned no text content")
        return text.strip()
    except Exception as e:
        logger.warning("Model %s attempt %d failed: %s", model, attempt + 1, e)
        if attempt < MAX_RETRIES - 1:
            delay = (RETRY_DELAY * (2 ** attempt)) + random.uniform(0, 1)
            time.sleep(delay)
            return _call_model_sync(contents, model_index, attempt + 1, system_instruction)
        if model_index + 1 < len(MODELS):
            logger.info("Falling back to model: %s", MODELS[model_index + 1])
            return _call_model_sync(contents, model_index + 1, 0, system_instruction)
        raise RuntimeError("All models and retries exhausted")





def _identify_product_sync(image_bytes: bytes) -> dict:
    prompt = f"""{ARD_IDENTITY}

You are a product recognition system for Ethiopian businesses.
Look at this product image and return ONLY a JSON object:
{{
  "name": "short product name in English or Amharic (max 5 words)",
  "description": "very brief description (max 10 words)"
}}
If unsure, return {{"name": "unknown", "description": "unknown"}}."""
    try:
        text = _call_model_sync(types.Content(
            parts=[
                types.Part(text=prompt),
                types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=image_bytes)),
            ]
        ))
        result = _parse_json_safely(text)
        if isinstance(result, dict) and result.get("name"):
            return result
        return {"name": "unknown", "description": "unknown"}
    except Exception as e:
        logger.error(f"Product identification error: {e}")
        return {"name": "unknown", "description": "unknown"}


async def identify_product(image_bytes: bytes) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _identify_product_sync, image_bytes)


# ─── Payment Receipt Verification ────────────────────────────────────────

RECEIPT_PROMPT = """You are a payment receipt verification system for Ethiopian banks.
Look at this receipt screenshot and extract the following information as JSON:
{
  "bank": "CBE, Telebirr, Dashen, Awash, BOA, Zemen, or Unknown",
  "payer_name": "name of the person who paid",
  "receiver_name": "name of the recipient",
  "receiver_account": "account number or phone number of the recipient",
  "amount": 0.00,
  "currency": "ETB",
  "reference": "transaction reference number",
  "date": "date of transaction",
  "status": "SUCCESS or FAILED"
}
If you cannot read the receipt clearly, return {"status": "UNREADABLE"}.
Return ONLY the JSON object, no other text."""


def _verify_receipt_sync(image_bytes: bytes) -> dict:
    try:
        text = _call_model_sync(types.Content(
            parts=[
                types.Part(text=RECEIPT_PROMPT),
                types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=image_bytes)),
            ]
        ))
        result = _parse_json_safely(text)
        if isinstance(result, dict):
            return result
        return {"status": "UNREADABLE", "raw": text}
    except Exception as e:
        logger.error("Receipt verification error: %s", e)
        return {"status": "ERROR", "error": str(e)}


async def verify_receipt(image_bytes: bytes) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _verify_receipt_sync, image_bytes)


# ─── Conversational Registration ────────────────────────────────────────────

REGISTRATION_SYSTEM_PROMPT = """You are a registration assistant for Ardi AI, helping Ethiopian business owners set up their account.

Collect these 4 things in a natural conversation:
1. Business name
2. What they sell / do
3. Address
4. Phone number

HOW TO TALK:
- Match their language — Amharic, English, mixed, whatever they use
- Don't introduce yourself or explain what you're doing. Just ask naturally
- One question at a time. No lists. No bullet points
- If they give a short answer, just move to the next question naturally
- Don't say "great", "perfect", "awesome" after every answer — real people don't

When you have all 4 pieces, end with:
===COMPLETE===
{{"name": "...", "description": "...", "address": "...", "phone": "..."}}"""


def _registration_chat_sync(conversation: list) -> dict:
    try:
        contents = []
        for msg in conversation:
            contents.append(types.Content(
                parts=[types.Part(text=msg["text"])],
                role="model" if msg["role"] == "assistant" else "user",
            ))
        if not contents:
            contents = [types.Content(
                parts=[types.Part(text="The user wants to register a new business. Greet them and ask for their business name.")],
                role="user",
            )]
        text = _call_model_sync(contents, system_instruction=f"{ARD_IDENTITY}\n\n{REGISTRATION_SYSTEM_PROMPT}")
        if "===COMPLETE===" in text:
            parts = text.split("===COMPLETE===")
            reply = parts[0].strip()
            data = _parse_json_safely(parts[1].strip())
            if isinstance(data, dict) and data.get("name"):
                return {"type": "complete", "reply": reply, "data": data}
            return {"type": "continue", "reply": reply + "\n\nI missed some details — could you repeat your business name?"}
        else:
            return {"type": "continue", "reply": text}
    except Exception as e:
        logger.error(f"Registration chat error: {e}")
        return {"type": "error", "reply": "Had a hiccup — could you say that again?"}


async def conduct_registration(conversation: list) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _registration_chat_sync, conversation)


# ─── Sales Agent (customer-facing) ─────────────────────────────────────────

SALES_SYSTEM_PROMPT = """{ARD_IDENTITY}

You are the sales assistant for {business_name}. Your ONLY job: answer product questions and take customer orders.

BUSINESS FACTS:
- Name: {business_name}
- About: {description}
- Address: {address}
- Phone: {phone}

PRODUCTS AVAILABLE:
{products_available}

PRODUCTS UNAVAILABLE (do NOT offer these):
{products_unavailable}

TONE ({tone}):
{tone_guide}

RECENT CONVERSATION:
{conversation_history}

FORBIDDEN ACTIONS (never do any of these):
- Never ask the customer to send a photo, image, or picture of anything.
- Never ask the customer to create, register, add, or edit anything.
- Never offer to add or remove products from the shop.
- Never make up product names, prices, images, or details not listed above.
- Never mention technical terms like "AI", "system", "bot", "registration", "database".
- Never ask "What would you like to order?" or "What do you want?" — you are mid-conversation.

PHOTO REQUESTS: If the customer asks to see a product photo, describe the product using the caption info provided above. If no caption exists, say something like "I can describe it — it's [product name]. Would you like to know the price or details?" Never say "I can't send photos" — just describe what you know.

RULES:
- Be short. 1-3 sentences max. No paragraphs.
- Match their language — Amharic, English, or mixed. Whatever they use, use it back.
- {lang_instruction}
- If a product is unavailable, say it's out of stock. Suggest an alternative only if the list has one.
- If asked about something not in the product list, say you don't have it. Do not invent products.
- Only answer what was asked. Do not add extra info, suggestions, or questions unless needed for the order flow.
- Never mention Gemini, Google, or AI. You are Ardi.

ORDER FLOW (follow exactly when the customer wants to buy):
Step 1 - Collect items. When they ask for a product, confirm and add to cart. Ask "What else?" only once per item.
Step 2 - When they say "that's all" / "done" / "that's it" — STOP asking for items. Tell them the total.
Step 3 - Ask for delivery info: 1) Name 2) Phone 3) Address. Do NOT ask for items during this step.
Step 4 - When you have all items + all 3 delivery fields, end with:

===ORDER===
{{"items": [{{"product": "exact product name", "quantity": 1}}], "customer_name": "...", "customer_phone": "...", "customer_address": "..."}}

CRITICAL: Once you say the total, never ask for more items. You are collecting delivery info now.

{order_payment_info}

AFTER AN ORDER IS PLACED:
Do not ask "what would you like" or "what else". The current order is done.
If the customer asks about their order, check the conversation and answer.
If the customer makes small talk, respond briefly and warmly.
If they want to order again, start a new order flow.

If the customer asks for something you cannot handle (complaints, discounts, owner contact, refunds, or anything outside simple product questions and orders), end with:

===ESCALATE===
{{"reason": "brief explanation"}}

Keep collecting items and info naturally. No marker until complete."""


def _format_product(p: dict) -> str:
    parts = [f"- {p.get('name', 'Unknown')}"]
    if p.get("price"):
        parts.append(f": {p['price']} ETB")
    caption = p.get("photo_caption")
    if caption and caption != "unknown":
        parts.append(f" — {caption}")
    return " ".join(parts)


def _parse_json_safely(text: str) -> dict | None:
    """Parse a JSON object from text. Returns None if result is not a dict."""
    import re
    text = text.strip()
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1:
        text = text[brace_start:brace_end+1]
    else:
        return None
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _sales_chat_sync(business_info: dict, products_text: str, products_unavailable_text: str, customer_message: str, tone: str, history_text: str = "", order_payment_info: str = "", lang: str = "en") -> dict:
    try:
        tone_guide = TONES.get(tone, TONES["friendly"])

        def _escape(s):
            return str(s).replace("{", "{{").replace("}", "}}")

        lang_instruction = "The customer prefers Amharic. Respond in Amharic whenever possible." if lang == "am" else "Respond naturally in whichever language the customer uses."
        prompt = SALES_SYSTEM_PROMPT.format(
            ARD_IDENTITY=ARD_IDENTITY,
            business_name=_escape(business_info.get("name", "the business")),
            description=_escape(business_info.get("description", "")),
            address=_escape(business_info.get("address", "")),
            phone=_escape(business_info.get("phone", "")),
            products_available=_escape(products_text or "No products listed yet."),
            products_unavailable=_escape(products_unavailable_text or "None"),
            tone=_escape(tone),
            tone_guide=_escape(tone_guide),
            conversation_history=_escape(history_text or "No prior conversation."),
            order_payment_info=_escape(order_payment_info or "No special payment instructions."),
            lang_instruction=_escape(lang_instruction),
        )
        text = _call_model_sync([prompt, customer_message])

        if "===ESCALATE===" in text:
            parts = text.split("===ESCALATE===")
            reply = parts[0].strip()
            raw = _parse_json_safely(parts[1].strip()) if len(parts) > 1 else None
            data = raw if isinstance(raw, dict) else {"reason": "unspecified"}
            return {"type": "escalate", "reply": reply, "data": data}

        if "===ORDER===" in text:
            parts = text.split("===ORDER===")
            reply = parts[0].strip()
            data = _parse_json_safely(parts[1].strip()) if len(parts) > 1 else None
            if isinstance(data, dict) and "items" in data:
                return {"type": "order", "reply": reply, "data": data}
            if isinstance(data, dict) and "product" in data:
                data["items"] = [{"product": data["product"], "quantity": data.get("quantity", 1)}]
                return {"type": "order", "reply": reply, "data": data}
            return {"type": "chat", "reply": reply + "\n\nI missed some order details. Could you repeat that?"}

        return {"type": "chat", "reply": text}
    except Exception as e:
        import traceback
        logger.error("Sales response error: %s\n%s", e, traceback.format_exc())
        return {"type": "chat", "reply": "Sorry, give me a moment — what did you ask again?"}


async def generate_sales_response(business_info: dict, products: list, customer_message: str, tone: str = "friendly", history: list | None = None, order_payment_info: str = "", lang: str = "en") -> dict:
    available = [p for p in products if p.get("available", True)]
    unavailable = [p for p in products if not p.get("available", True)]

    products_text = "\n".join(
        _format_product(p)
        for p in available[:30]
    ) if available else "No products listed yet."
    if len(available) > 30:
        products_text += f"\n... and {len(available) - 30} more products."

    products_unavailable_text = "\n".join(
        f"- {p.get('name', 'Unknown')}" for p in unavailable[:20]
    ) if unavailable else ""
    if len(unavailable) > 20:
        products_unavailable_text += f"\n... and {len(unavailable) - 20} more."

    history_text = ""
    if history:
        lines = []
        for m in history[-6:]:
            role = "Customer" if m["role"] == "user" else "Ardi"
            lines.append(f"{role}: {m['text'][:200]}")
        history_text = "\n".join(lines)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sales_chat_sync, business_info, products_text, products_unavailable_text, customer_message, tone, history_text, order_payment_info, lang)


# ─── Intent-Based Agent ──────────────────────────────────────────────
# Flow: User → Gemini classifies intent → Backend routes to handler → User

INTENTS_REGISTRY = {
    "add_product": {
        "description": "Add a new product to inventory",
        "params": "Parameters: product_name (string), price (number, in ETB)",
        "examples": ['"Add bread 45 birr"', '"New product oil 60"'],
        "roles": ["business_owner"],
    },
    "delete_product": {
        "description": "Remove a product from inventory",
        "params": "Parameters: product_name (string)",
        "examples": ['"Remove bread"', '"Delete milk"'],
        "roles": ["business_owner"],
    },
    "change_price": {
        "description": "Change a product's price",
        "params": "Parameters: product_name (string), new_price (number, in ETB)",
        "examples": ['"Change bread to 50"', '"Set oil price 55"'],
        "roles": ["business_owner"],
    },
    "set_availability": {
        "description": "Mark a product in stock or out of stock",
        "params": "Parameters: product_name (string), available (true/false)",
        "examples": ['"Mark milk out of stock"', '"Bread is back in stock"'],
        "roles": ["business_owner"],
    },
    "view_catalog": {
        "description": "Show all products",
        "params": "Parameters: none",
        "examples": ['"Show my products"', '"What do I have?"', '"List inventory"'],
        "roles": ["business_owner", "guest"],
    },
    "view_orders": {
        "description": "Show orders (optionally filtered by status)",
        "params": "Parameters: status (string, optional — pending/confirmed/completed/cancelled)",
        "examples": ['"Show orders"', '"Any new orders?"'],
        "roles": ["business_owner"],
    },
    "view_settings": {
        "description": "Show current AI settings (status, tone)",
        "params": "Parameters: none",
        "examples": ['"What are my settings?"', '"AI status"'],
        "roles": ["business_owner"],
    },
    "change_tone": {
        "description": "Change the AI conversation tone",
        "params": "Parameters: tone (string — friendly/professional/casual/formal/witty)",
        "examples": ['"Change tone to professional"', '"Make it casual"'],
        "roles": ["business_owner"],
    },
    "toggle_ai": {
        "description": "Turn the AI assistant on or off",
        "params": "Parameters: none",
        "examples": ['"Turn off AI"', '"Enable AI"'],
        "roles": ["business_owner"],
    },
    "get_share_link": {
        "description": "Get the shareable customer link",
        "params": "Parameters: none",
        "examples": ['"Get my link"', '"Share link"'],
        "roles": ["business_owner"],
    },
    "register_business": {
        "description": "Register a new business",
        "params": "Parameters: none (conversation will collect details)",
        "examples": ['"I want to register"', '"Start my business"'],
        "roles": ["guest"],
    },
    "set_business_hours": {
        "description": "Set business hours",
        "params": "Parameters: start (string, HH:MM), end (string, HH:MM)",
        "examples": ['"Set hours 09:00 to 18:00"'],
        "roles": ["business_owner"],
    },
    "set_offline_message": {
        "description": "Set the message customers see outside business hours",
        "params": "Parameters: message (string)",
        "examples": ['"Set offline message to We are closed"'],
        "roles": ["business_owner"],
    },
    "browse_businesses": {
        "description": "Browse businesses available to chat with",
        "params": "Parameters: none",
        "examples": ['"Show businesses"', '"What businesses are there?"'],
        "roles": ["guest", "business_owner"],
    },
    "show_help": {
        "description": "Show available commands and features",
        "params": "Parameters: none",
        "examples": ['"Help"', '"What can you do?"'],
        "roles": ["guest", "business_owner"],
    },
}


STATE_RULES = {
    "awaiting_product_name": (
        "The user is CURRENTLY ADDING A PRODUCT. They told you they want to add something. "
        "Now they are telling you the product name. "
        "Your ONLY possible intent here is 'add_product'. Extract: product_name (string) from their message. "
        "Do NOT consider 'change_price', 'delete_product', or any other intent. "
        "If their message is a greeting, thanks, or unrelated chat, respond with chat type instead."
    ),
    "awaiting_product_price": (
        "The user is CURRENTLY ADDING A PRODUCT. They already gave you the product name. "
        "Now they are telling you the price. "
        "Your ONLY possible intent here is 'add_product'. Extract: price (number, in ETB) from their message. "
        "Do NOT consider 'change_price', 'delete_product', or any other intent — the user is ADDING, not changing. "
        "If their message is a greeting, thanks, or unrelated chat, respond with chat type instead."
    ),
}

def _build_intent_prompt(context: dict) -> str:
    """Build the intent agent prompt filtered by the user's role."""
    role = context.get("role", "guest")
    state = context.get("state", "idle")
    lines = [f"{ARD_IDENTITY}"]
    lines.append("")
    lines.append(f"You are the AI assistant for {context.get('business_name', 'Ardi AI')}.")
    lines.append("")
    lines.append("Current context:")
    lines.append(f"- Owner: {context.get('owner_name', '—')}")
    lines.append(f"- Role: {role}")
    lines.append(f"- Today: {context.get('date', '')}")
    lines.append("")

    lines.append("Products:")
    lines.append(context.get("products_text", "No products."))
    lines.append("")

    if state != "idle":
        rule = STATE_RULES.get(state, "The user is in the middle of a previous action.")
        lines.append(f"⚠ Active state: {state}")
        lines.append(f"What to do: {rule}")
        lines.append("If the user's message answers the expected info, return it as action params.")
        lines.append('If the user says something unrelated (greeting, thanks, changing topic), return {"type": "chat", "reply": "..."} and the backend will cancel the current action.')
        lines.append("")

    if role == "guest":
        lines.append("NOTE: You are talking to a guest who has NOT registered a business yet.")
        lines.append("You cannot perform business actions for them. Only guide them to register.")
        lines.append("")

    lines.append("Available actions:")
    for name, info in INTENTS_REGISTRY.items():
        if role in info["roles"] or "all" in info["roles"]:
            lines.append(f"- {name}: {info['description']}")
            lines.append(f"  {info['params']}")
            lines.append(f'  Examples: {", ".join(info["examples"])}')
            lines.append("")

    lines.append("Rules:")
    lines.append("1. If the user wants to perform an action (or is continuing one), respond with:")
    lines.append('   {"type": "action", "intent": "intent_name", "params": {...}}')
    lines.append("   Include ALL parameters the user provides. Extract what you can.")
    lines.append('')
    lines.append("   CRITICAL — Never make up or invent product names, prices, or any data.")
    lines.append("   If the user did not specify a value, leave the param empty or omit it.")
    lines.append("   Do NOT use example product names from the list below as real products.")
    lines.append("   If asked to add 'a product' with no name, respond as chat asking for the name.")
    lines.append("")
    lines.append("2. If the user is just chatting (greeting, thanks, unrelated question, changing topic),")
    lines.append("   respond with:")
    lines.append('   {"type": "chat", "reply": "your friendly response"}')
    lines.append("")
    lines.append("3. Match the user's language — Amharic, English, or mixed.")
    lines.append("4. Be concise (1-3 sentences). Never mention Gemini, Google, or AI.")
    return "\n".join(lines)


def _classify_intent_sync(context: dict, message: str) -> dict:
    """Classify user intent using Gemini."""
    try:
        prompt = _build_intent_prompt(context)
        text = _call_model_sync([prompt, message])
        result = _parse_json_safely(text)
        if isinstance(result, dict) and "type" in result:
            return result
        if isinstance(result, dict):
            logger.warning("Gemini returned JSON without 'type' key: %s", result)
            return {"type": "chat", "reply": "I understood, but could you rephrase that?"}
        if text:
            return {"type": "chat", "reply": text}
        return {"type": "chat", "reply": "I'm here! What can I help you with?"}
    except Exception as e:
        logger.error("Intent classification error: %s", e)
        return {"type": "chat", "reply": "I'm here! What can I help you with?"}


async def classify_intent(context: dict, message: str) -> dict:
    """Classify user intent asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _classify_intent_sync, context, message)
