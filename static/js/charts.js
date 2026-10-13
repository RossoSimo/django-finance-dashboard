/* FinanceDash charts: shared Chart.js defaults and range switching.
 *
 * FD.charts.line(canvas, payload, seriesConfig)      -> chart (or null)
 * FD.charts.doughnut(canvas, labels, values, colors) -> chart (or null)
 * FD.charts.bindRanges(navEl, chart, endpointUrl, onStats)
 *
 * seriesConfig: [{key, label, color, fill, dashed, stepped, width}], where
 * `key` names an entry of payload.series and `color` is a CSS variable name
 * ("--fd-accent") or a literal colour. Trade markers come from payload.trades.
 *
 * Payloads come from performance/payloads.py. Colours are read from the
 * --fd-* CSS variables and re-read on "fd:themechange". Money is formatted
 * here only for axes and tooltips; stat strings arrive pre-formatted.
 */
(function () {
    "use strict";

    window.FD = window.FD || {};
    var charts = [];  // live charts, recoloured on theme change

    function cssVar(name, fallback) {
        var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback || "";
    }

    // "--fd-accent" -> its current value; any other string is used as is.
    function resolveColor(color) {
        return typeof color === "string" && color.indexOf("--") === 0 ? cssVar(color, "#888") : color;
    }

    function tint(color) {
        return /^#[0-9a-f]{6}$/i.test(color) ? color + "1a" : color;
    }

    function money(value, currency, maxDecimals) {
        if (value === null || value === undefined) { return "—"; }
        try {
            return new Intl.NumberFormat("it-IT", {
                style: "currency", currency: currency || "EUR",
                minimumFractionDigits: 2, maximumFractionDigits: maxDecimals || 2
            }).format(value);
        } catch (e) {
            return String(value);
        }
    }

    // Daily labels are "YYYY-MM-DD" (calendar dates, no zone); intraday labels
    // are ISO instants shown in the browser's time zone.
    function parseLabel(label, intraday) {
        if (intraday) { return new Date(label); }
        var p = label.split("-");
        return new Date(+p[0], +p[1] - 1, +p[2]);
    }

    function formatLabel(label, payload, full) {
        var date = parseLabel(label, payload.intraday);
        if (isNaN(date.getTime())) { return label; }
        var opts;
        if (payload.intraday) {
            opts = payload.range === "7d"
                ? { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }
                : { hour: "2-digit", minute: "2-digit", hour12: false };
        } else if (full) {
            opts = { day: "numeric", month: "short", year: "numeric" };
        } else if (payload.range === "1y" || payload.range === "max") {
            opts = { month: "short", year: "2-digit" };
        } else {
            opts = { day: "numeric", month: "short" };
        }
        return new Intl.DateTimeFormat("it-IT", opts).format(date);
    }

    function themeColors() {
        return {
            text: cssVar("--fd-muted", "#666"),
            grid: cssVar("--fd-border", "#ddd"),
            surface: cssVar("--fd-surface", "#fff")
        };
    }

    function seriesDataset(cfg, values) {
        var color = resolveColor(cfg.color);
        return {
            type: "line", label: cfg.label, data: values, _color: cfg.color,
            borderColor: color, borderWidth: cfg.width || 2,
            backgroundColor: cfg.fill ? tint(color) : "transparent",
            fill: !!cfg.fill, stepped: cfg.stepped || false, borderDash: cfg.dashed ? [6, 4] : [],
            tension: cfg.stepped ? 0 : 0.2, pointRadius: 0, pointHoverRadius: 4, spanGaps: true
        };
    }

    function tradeDatasets(payload) {
        var kinds = [
            ["buy", "Buy", "--fd-positive", "triangle"],
            ["sell", "Sell", "--fd-negative", "rectRot"]
        ];
        var result = [];
        kinds.forEach(function (k) {
            var points = (payload.trades || []).filter(function (t) { return t.type === k[0]; });
            if (points.length) {
                result.push({
                    type: "scatter", label: k[1], data: points, _color: k[2], _marker: true,
                    backgroundColor: resolveColor(k[2]), borderColor: resolveColor(k[2]),
                    pointStyle: k[3], pointRadius: 7, pointHoverRadius: 8
                });
            }
        });
        return result;
    }

    function buildDatasets(chart) {
        var payload = chart.fdPayload;
        var datasets = [];
        chart.fdSeries.forEach(function (cfg) {
            var values = (payload.series || {})[cfg.key] || [];
            if (values.length) { datasets.push(seriesDataset(cfg, values)); }
        });
        return datasets.concat(tradeDatasets(payload));
    }

    function applyTheme(chart) {
        var t = themeColors();
        var scales = chart.options.scales;
        if (scales && scales.x) { scales.x.ticks.color = t.text; }
        if (scales && scales.y) {
            scales.y.ticks.color = t.text;
            scales.y.grid.color = t.grid;
        }
        chart.data.datasets.forEach(function (ds) {
            if (ds._color === undefined) { return; }
            var c = resolveColor(ds._color);
            ds.borderColor = c;
            if (ds._marker) { ds.backgroundColor = c; }
            else if (ds.fill) { ds.backgroundColor = tint(c); }
        });
        if (chart.fdColors) {
            chart.data.datasets[0].backgroundColor = chart.fdColors.map(resolveColor);
            chart.data.datasets[0].borderColor = t.surface;
        }
    }

    function line(canvas, payload, seriesConfig) {
        if (!canvas || typeof Chart === "undefined") { return null; }
        var t = themeColors();
        var decimals = (seriesConfig || []).some(function (c) { return c.key === "price"; }) ? 4 : 2;
        var chart = new Chart(canvas, {
            type: "line",
            data: { labels: payload.labels, datasets: [] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function (items) {
                                return items.length ? formatLabel(items[0].label, chart.fdPayload, true) : "";
                            },
                            label: function (ctx) {
                                var v = money(ctx.parsed.y, chart.fdPayload.currency, decimals);
                                if (ctx.dataset.type === "scatter") {
                                    return ctx.dataset.label + " " + ctx.raw.quantity + " @ " + v;
                                }
                                return ctx.dataset.label + ": " + v;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: "category", grid: { display: false },
                        ticks: {
                            color: t.text, maxTicksLimit: 6, maxRotation: 0, autoSkip: true,
                            callback: function (value) {
                                return formatLabel(this.getLabelForValue(value), chart.fdPayload, false);
                            }
                        }
                    },
                    y: {
                        grid: { color: t.grid },
                        ticks: {
                            color: t.text,
                            callback: function (value) { return money(value, chart.fdPayload.currency, decimals); }
                        }
                    }
                }
            }
        });
        chart.fdSeries = seriesConfig || [];
        chart.fdPayload = payload;
        chart.fdUpdate = function (next) {
            chart.fdPayload = next;
            chart.data.labels = next.labels;
            chart.data.datasets = buildDatasets(chart);
            chart.update();
        };
        chart.fdUpdate(payload);
        charts.push(chart);
        return chart;
    }

    function doughnut(canvas, labels, values, colors) {
        if (!canvas || typeof Chart === "undefined") { return null; }
        var chart = new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [{
                    data: values, backgroundColor: colors.map(resolveColor),
                    borderColor: themeColors().surface, borderWidth: 2
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: "68%",
                plugins: { legend: { display: false } }
            }
        });
        chart.fdColors = colors;
        charts.push(chart);
        return chart;
    }

    function bindRanges(navEl, chart, endpointUrl, onStats) {
        if (!navEl || !chart || !window.fetch) { return; }
        var requestId = 0;

        function rangeOf(a) {
            return new URL(a.getAttribute("href"), window.location.href).searchParams.get("range");
        }

        function setActive(key) {
            navEl.querySelectorAll("a[href]").forEach(function (a) {
                var active = rangeOf(a) === key;
                a.classList.toggle("active", active);
                if (active) { a.setAttribute("aria-current", "page"); }
                else { a.removeAttribute("aria-current"); }
            });
        }

        function showStats(payload) {
            var stats = payload.stats || {};
            document.querySelectorAll("[data-stat]").forEach(function (el) {
                var value = stats[el.getAttribute("data-stat")];
                if (value !== undefined) { el.textContent = value; }
                if (stats.direction) { el.setAttribute("data-direction", stats.direction); }
            });
            if (typeof onStats === "function") { onStats(payload); }
        }

        navEl.addEventListener("click", function (event) {
            var link = event.target.closest("a[href]");
            if (!link || !navEl.contains(link) || event.defaultPrevented ||
                event.metaKey || event.ctrlKey || event.shiftKey || event.button) { return; }
            var key = rangeOf(link);
            if (!key) { return; }
            event.preventDefault();
            var mine = ++requestId;
            var url = endpointUrl + "?range=" + encodeURIComponent(key);
            if (navEl.dataset.holding) { url += "&holding=" + encodeURIComponent(navEl.dataset.holding); }
            navEl.setAttribute("aria-busy", "true");
            fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
                .then(function (response) {
                    if (!response.ok) { throw new Error("HTTP " + response.status); }
                    return response.json();
                })
                .then(function (payload) {
                    if (mine !== requestId) { return; }  // a newer click won
                    chart.fdUpdate(payload);
                    setActive(payload.range);
                    showStats(payload);
                    var here = new URL(window.location.href);
                    here.searchParams.set("range", payload.range);
                    window.history.replaceState(null, "", here);
                })
                .catch(function () {
                    if (mine === requestId) { window.location.href = link.href; }  // plain page load
                })
                .finally(function () {
                    if (mine === requestId) { navEl.removeAttribute("aria-busy"); }
                });
        });
    }

    document.addEventListener("fd:themechange", function () {
        charts.forEach(function (chart) {
            applyTheme(chart);
            chart.update();
        });
    });

    FD.charts = { line: line, doughnut: doughnut, bindRanges: bindRanges };
})();
