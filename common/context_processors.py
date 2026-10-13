def nav(request):
    """Tell the app shell which navigation item to highlight."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return {"nav_active": ""}
    namespace = match.namespace or ""
    url_name = match.url_name or ""
    if namespace == "assistant":
        return {"nav_active": "import" if url_name.startswith("import") else "assistant"}
    if namespace == "newsfeed":
        return {"nav_active": "news"}
    if namespace in ("dashboard", "wallet", "performance", "watchlist", "alerts"):
        return {"nav_active": namespace}
    if url_name == "settings" or url_name.startswith("password_change"):
        return {"nav_active": "settings"}
    return {"nav_active": ""}
