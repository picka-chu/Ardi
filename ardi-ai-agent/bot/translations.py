"""Simple English / Amharic translation dictionary for bot messages."""

TR = {
    # Language selection
    "choose_language": {
        "en": "🌐 Please choose your language / እባክዎ ቋንቋዎን ይምረጡ",
        "am": "🌐 እባክዎ ቋንቋዎን ይምረጡ / Please choose your language",
    },
    "lang_selected_en": {"en": "Language set to English 🇬🇧", "am": "ቋንቋ ወደ እንግሊዝኛ ተቀይሯል 🇬🇧"},
    "lang_selected_am": {"en": "Language set to Amharic 🇪🇹", "am": "ቋንቋ ወደ አማርኛ ተቀይሯል 🇪🇹"},

    # Registration
    "welcome_registered": {
        "en": "Welcome back, {name}! What would you like to do?",
        "am": "እንኳን ደህና መጡ፣ {name}! ምን ማድረግ ይፈልጋሉ?",
    },
    "welcome_unregistered": {
        "en": "Welcome to Ardi AI! 🚀\nI help Ethiopian businesses automate sales with AI.\n\nTap 🚀 Register My Business to get started, or tap 🔍 Browse Businesses to explore.",
        "am": "እንኳን ወደ አርዲ AI በደህና መጡ! 🚀\nየኢትዮጵያ ንግዶች ሽያጭን በ AI እንዲያሳድጉ እረዳለሁ።\n\nለመጀመር 🚀 ንግዴን አስመዝግብ የሚለውን ይንኩ፣ ወይም ለማሰስ 🔍 ንግዶችን ያስሱ የሚለውን ይንኩ።",
    },

    # Common
    "register_first": {"en": "Register first with /register.", "am": "በመጀመሪያ በ /register ይመዝገቡ።"},
    "cancelled": {"en": "Cancelled.", "am": "ተሰርዟል።"},
    "no_orders": {"en": "No orders yet.", "am": "እስካሁን ምንም ትዕዛዞች የሉም።"},
    "main_menu": {"en": "🏠 Main Menu", "am": "🏠 ዋና ሜኑ"},
    "back": {"en": "🔙 Back", "am": "🔙 ተመለስ"},
    "error_generic": {"en": "Something went wrong. Please try again.", "am": "የሆነ ስህተት ተከስቷል። እባክዎ እንደገና ይሞክሩ።"},
    "rate_limit": {"en": "⏳ You're sending messages too fast. Please slow down.", "am": "⏳ በጣም በፍጥነት እየላኩ ነው። እባክዎ በዝግታ ይላኩ።"},
    "no_businesses": {"en": "No businesses are currently available.\nCheck back later!", "am": "በአሁኑ ጊዜ ምንም ንግዶች የሉም።\nበኋላ ይመልከቱ!"},

    # Chat
    "welcome_chat_with": {
        "en": "👋 You are now chatting with *{name}*!\nAsk me about their products, prices, and more.\n\nSend /endchat to stop.",
        "am": "👋 አሁን ከ *{name}* ጋር እየተነጋገሩ ነው!\nስለ ምርቶቻቸው፣ ዋጋዎቻቸው እና ሌሎችም ይጠይቁ።\n\nለማቆም /endchat ይላኩ።",
    },

    # Subscription
    "sub_expired": {
        "en": "⚠️ *Subscription Expired*\nYour {label}. AI features are locked.\n\n• Monthly: {monthly:,} ETB\n• Yearly: {yearly:,} ETB (2 months free)\n\nUse /plans to subscribe.",
        "am": "⚠️ *የደንበኝነት ምዝገባ ጊዜው አልፏል*\n{label}። የ AI ባህሪያት ተቆልፈዋል።\n\n• ወርሃዊ፦ {monthly:,} ብር\n• ዓመታዊ፦ {yearly:,} ብር (2 ወር ነፃ)\n\nለመመዝገብ /plans ይጠቀሙ።",
    },

    # Chat
    "end_chat": {
        "en": "Chat ended. Thanks for visiting!\nUse /businesses to find other businesses.",
        "am": "ውይይት ተጠናቋል። ስለጎበኙን እናመሰግናለን!\nሌሎች ንግዶችን ለማግኘት /businesses ይጠቀሙ።",
    },
    "business_unavailable": {
        "en": "This business is no longer available.",
        "am": "ይህ ንግድ አይገኝም።",
    },
    "business_expired": {
        "en": "This business's subscription has expired. They are currently unavailable.",
        "am": "የዚህ ንግድ የደንበኝነት ምዝገባ ጊዜው አልፏል። በአሁኑ ጊዜ አይገኙም።",
    },
    "awaiting_payment_hint": {
        "en": "Please send a screenshot of your payment receipt so I can confirm your order.",
        "am": "እባክዎ የክፍያ ደረሰኝዎን ቅጽበታዊ ገጽ እይታ ይላኩ ስለዚህ ትዕዛዝዎን ማረጋገጥ እችላለሁ።",
    },

    # Product
    "no_products": {"en": "No products yet.", "am": "እስካሁን ምንም ምርቶች የሉም።"},
    "product_added": {"en": "✅ *{name}* saved!\nPrice: *{price:.2f} ETB*", "am": "✅ *{name}* ተቀምጧል!\nዋጋ፦ *{price:.2f} ብር*"},
}


def _t(key: str, lang: str = "en", **kwargs) -> str:
    """Get translated text for the given key and language."""
    entry = TR.get(key)
    if not entry:
        return f"[missing translation: {key}]"
    text = entry.get(lang, entry.get("en", ""))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def lang_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
         InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="lang_am")],
    ])
