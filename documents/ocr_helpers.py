def ocr_data_to_form_initial(data) -> dict:
    if not isinstance(data, dict):
        return {}

    products_data = data.get("products") or []
    return {
        "title": data.get("title"),
        "products": (
            "\n".join(products_data)
            if isinstance(products_data, list)
            else products_data
        ),
        "merchant": data.get("merchant"),
        "balance": data.get("balance"),
        "transaction_date": data.get("transaction_date"),
        "expiry_date": data.get("expiry_date"),
        "record_type": data.get("record_type"),
    }