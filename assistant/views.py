from django.contrib import messages
from django.shortcuts import redirect, render

from wallet.models import CashAccount, Holding

from .context import build_portfolio_context
from .forms import (
    CashImportFormSet,
    HoldingImportFormSet,
    cash_initial_from_extracted,
    holding_initial_from_extracted,
)
from .ollama_client import OllamaError, ask_portfolio_question, extract_portfolio_from_screenshot

# Quick-action prompts shown as buttons on the chat page.
SUGGESTED_PROMPTS = [
    "How diversified is my portfolio?",
    "What are my biggest winners and losers right now?",
    "Any concentration risk I should be aware of?",
    "Summarize my watchlist versus what I currently hold.",
]


def chat(request):
    history = request.session.get("assistant_chat_history", [])

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        if question:
            context_json = build_portfolio_context(request.user)
            try:
                answer = ask_portfolio_question(question, context_json)
            except OllamaError as exc:
                answer = f"⚠️ {exc}"
            history.append({"question": question, "answer": answer})
            request.session["assistant_chat_history"] = history[-20:]
        return redirect("assistant:chat")

    return render(request, "assistant/chat.html", {
        "history": history,
        "suggested_prompts": SUGGESTED_PROMPTS,
    })


def clear_chat(request):
    request.session.pop("assistant_chat_history", None)
    return redirect("assistant:chat")


def import_upload(request):
    if request.method == "POST":
        image = request.FILES.get("screenshot")
        if not image:
            messages.error(request, "Please choose an image to upload.")
            return redirect("assistant:import_upload")

        image_bytes = image.read()
        try:
            extracted = extract_portfolio_from_screenshot(image_bytes)
        except OllamaError as exc:
            messages.error(request, str(exc))
            return redirect("assistant:import_upload")

        if not extracted["cash_accounts"] and not extracted["holdings"]:
            messages.warning(request, "Nothing recognizable was found in that screenshot. Try a clearer image.")
            return redirect("assistant:import_upload")

        request.session["import_extracted"] = extracted
        return redirect("assistant:import_preview")

    return render(request, "assistant/import_upload.html")


def import_preview(request):
    extracted = request.session.get("import_extracted")
    if not extracted:
        messages.error(request, "No screenshot data to review — please upload a screenshot first.")
        return redirect("assistant:import_upload")

    if request.method == "POST":
        holding_formset = HoldingImportFormSet(request.POST, prefix="holdings")
        cash_formset = CashImportFormSet(request.POST, prefix="cash")

        if holding_formset.is_valid() and cash_formset.is_valid():
            imported = 0

            for form in holding_formset:
                cd = form.cleaned_data
                if not cd or not cd.get("include") or not cd.get("ticker"):
                    continue
                Holding.objects.update_or_create(
                    user=request.user,
                    ticker=cd["ticker"],
                    defaults={
                        "name": cd.get("name", ""),
                        "asset_type": cd["asset_type"],
                        "quantity": cd.get("quantity") or 0,
                        "avg_cost": cd.get("avg_cost") or 0,
                        "currency": cd.get("currency") or "USD",
                    },
                )
                imported += 1

            for form in cash_formset:
                cd = form.cleaned_data
                if not cd or not cd.get("include") or not cd.get("name"):
                    continue
                CashAccount.objects.update_or_create(
                    user=request.user,
                    name=cd["name"],
                    defaults={
                        "currency": cd.get("currency") or "USD",
                        "balance": cd.get("balance") or 0,
                    },
                )
                imported += 1

            request.session.pop("import_extracted", None)
            messages.success(request, f"Imported {imported} item(s) from the screenshot.")
            return redirect("wallet:home")
    else:
        holding_formset = HoldingImportFormSet(
            initial=holding_initial_from_extracted(extracted.get("holdings")), prefix="holdings"
        )
        cash_formset = CashImportFormSet(
            initial=cash_initial_from_extracted(extracted.get("cash_accounts")), prefix="cash"
        )

    return render(request, "assistant/import_preview.html", {
        "holding_formset": holding_formset,
        "cash_formset": cash_formset,
    })
