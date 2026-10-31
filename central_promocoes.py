"""Central de Promocoes de Jogos.

Consulta fontes gratuitas (Steam em R$, Epic, GamerPower e CheapShark),
gera uma pagina local (central.html) e avisa por notificacao do Windows
quando aparece jogo gratis ou promocao boa que voce ainda nao viu.

Uso:  python central_promocoes.py          -> atualiza e notifica
      python central_promocoes.py --abrir  -> atualiza e abre a pagina
"""
import base64
import html
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- ajustes
CONFIG = {
    "steam_paginas": 2,              # 100 jogos por pagina (mais vendidos em promocao)
    "steam_desconto_min": 40,        # % minimo pra aparecer na pagina
    "steam_nota_min": 70,            # % de avaliacoes positivas
    "cheapshark_desconto_min": 60,
    "cheapshark_nota_min": 75,
    "avisar_desconto_min": 80,       # promocao nova com esse desconto ou mais gera aviso
    "avisar_menor_preco": True,      # avisa quando a Steam bate o menor preco ja visto
    "gratis_plataformas": [],        # vazio = todas. Ex.: ["PC", "Steam", "Epic", "GOG"]
    "horas_acaba_logo": 48,
    "telegram_pelo_pc": False,       # False quando a nuvem (GitHub) ja estiver mandando
    "telegram_fotos": 3,            # quantos destaques vao como cartao com foto (o resto vai no resumo)
    # "famoso" = muitas avaliacoes na Steam, nota alta no Metacritic ou lancamento recente em alta
    "famoso_avaliacoes": 20000,
    "famoso_metacritic": 85,
    "lancamento_dias": 365,
    "lancamento_avaliacoes": 3000,
    "famoso_desconto_min": 60,       # famoso entra no destaque se gratis ou com esse desconto...
    "famoso_preco_max": 40,          # ...e custando ate isso em reais
    "famoso_desconto_qualquer_preco": 70,  # com esse desconto entra mesmo se for mais caro
    "cotacao_dolar": 5.5,            # so pra comparar precos das lojas em US$
    "famoso_buscas_por_vez": 15,     # limite de consultas de fama por execucao (fica em cache)
}

PASTA = Path(__file__).resolve().parent
ESTADO = PASTA / os.environ.get("CENTRAL_ESTADO", "estado.json")  # na nuvem fica em dados/
PAGINA = PASTA / "central.html"
LOG = PASTA / "erros.log"
UA = {"User-Agent": "CentralPromocoes/1.0"}
AGORA = time.time()


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def log_erro(fonte):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%d/%m %H:%M}] {fonte}\n{traceback.format_exc()}\n")


def iso_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def brl(centavos):
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def item(chave, tipo, titulo, loja, url, img="", preco="", original="", desconto=0,
         nota=None, fim=None, inicio=None, menor=False, avaliacoes=None, lancamento=None, meta=0):
    return dict(chave=chave, tipo=tipo, titulo=titulo, loja=loja, url=url, img=img,
                preco=preco, original=original, desconto=desconto, nota=nota,
                fim=fim, inicio=inicio, menor=menor, novo_menor=False,
                avaliacoes=avaliacoes, lancamento=lancamento, meta=meta,
                img2=img, economia="")


def capa_steam(appid):
    return f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"


def ler_resultado_steam(bloco):
    """Extrai dados de uma linha da busca da Steam (pagina em ingles)."""
    m = lambda rx: (re.search(rx, bloco, re.S) or [None, None])[1]
    nota = m(r'search_review_summary[^>]*data-tooltip-html="[^"]*?(\d+)% of the')
    qtd = m(r'of the ([\d,]+) user reviews')
    lanc = None
    try:
        lanc = datetime.strptime(m(r'search_released[^>]*>\s*(.*?)\s*<').strip(), "%d %b, %Y").timestamp()
    except Exception:
        pass
    return dict(appid=m(r'data-ds-appid="(\d+)"'), titulo=m(r'<span class="title">(.*?)</span>'),
                desc=m(r'data-discount="(\d+)"'), final=m(r'data-price-final="(\d+)"'),
                original=m(r'discount_original_price">(.*?)<') or "",
                nota=int(nota) if nota else None,
                avaliacoes=int(qtd.replace(",", "")) if qtd else 0, lancamento=lanc)


# ---------------------------------------------------------------- fontes
def fonte_epic():
    d = get_json("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
                 "?locale=pt-BR&country=BR")
    out = []
    for e in d["data"]["Catalog"]["searchStore"]["elements"]:
        promos = e.get("promotions") or {}
        slug = e.get("productSlug") or ""
        if not slug:
            maps = (e.get("catalogNs") or {}).get("mappings") or e.get("offerMappings") or []
            slug = maps[0]["pageSlug"] if maps else ""
        slug = slug.split("/")[0]
        url = f"https://store.epicgames.com/pt-BR/p/{slug}" if slug else "https://store.epicgames.com/pt-BR/free-games"
        imgs = {k["type"]: k["url"] for k in e.get("keyImages", [])}
        img = imgs.get("OfferImageWide") or imgs.get("DieselStoreFrontWide") or imgs.get("Thumbnail", "")
        for campo, tipo in (("promotionalOffers", "gratis"), ("upcomingPromotionalOffers", "embreve")):
            for grupo in promos.get(campo) or []:
                for p in grupo.get("promotionalOffers", []):
                    if (p.get("discountSetting") or {}).get("discountPercentage") != 0:
                        continue
                    out.append(item(f"epic:{e['id']}:{tipo}", tipo, e["title"], "Epic Games",
                                    url, img, "Gratis", desconto=100,
                                    fim=iso_ts(p.get("endDate", "")),
                                    inicio=iso_ts(p.get("startDate", ""))))
    return out


EPIC_LISTAS = {"top-sellers": "famoso", "most-played": "famoso", "top-player-reviewed": "famoso",
               "most-popular": "famoso", "top-new-releases": "lancamento"}
EPIC_QUERY = """query c($slug: String!, $country: String!, $locale: String) {
  Storefront { collectionLayout(locale: $locale, slug: $slug) { collectionOffers { title id productSlug urlSlug
    catalogNs { mappings(pageType: "productHome") { pageSlug } } keyImages { type url }
    price(country: $country) { totalPrice { discountPrice originalPrice } lineOffers { appliedRules { endDate } } } } } } }"""


def fonte_epic_promos():
    """Promocoes pagas da Epic, em R$, tiradas das listas de populares da loja."""
    vistos, out = set(), []
    for slug, fama in EPIC_LISTAS.items():
        url = "https://store.epicgames.com/graphql?" + urllib.parse.urlencode(
            {"query": EPIC_QUERY, "variables": json.dumps({"slug": slug, "country": "BR", "locale": "pt-BR"})})
        try:
            ofertas = get_json(url)["data"]["Storefront"]["collectionLayout"]["collectionOffers"]
        except Exception:
            log_erro(f"Epic lista {slug}")
            continue
        for o in ofertas:
            p = o["price"]["totalPrice"]
            final, orig = p["discountPrice"], p["originalPrice"]
            if o["id"] in vistos or not orig or final >= orig:
                continue
            desc = round(100 - final * 100 / orig)
            if desc < CONFIG["steam_desconto_min"]:
                continue
            vistos.add(o["id"])
            maps = (o.get("catalogNs") or {}).get("mappings") or []
            slug_p = (maps[0]["pageSlug"] if maps else o.get("productSlug") or o.get("urlSlug") or "").split("/")[0]
            imgs = {k["type"]: k["url"] for k in o.get("keyImages", [])}
            img = imgs.get("OfferImageWide") or imgs.get("DieselStoreFrontWide") or imgs.get("Thumbnail", "")
            fim = None
            for linha in o["price"].get("lineOffers") or []:
                for regra in linha.get("appliedRules") or []:
                    fim = fim or iso_ts(regra.get("endDate") or "")
            it = item(f"epic:{o['id']}:promo", "promo", o["title"], "Epic Games",
                      f"https://store.epicgames.com/pt-BR/p/{slug_p}", img, brl(final), brl(orig), desc, fim=fim)
            it.update(_centavos=final, valor=final / 100, economia=brl(orig - final), fama_fixa=fama)
            out.append(it)
    return out


def fonte_gamerpower():
    d = get_json("https://www.gamerpower.com/api/giveaways?type=game")
    filtro = [p.lower() for p in CONFIG["gratis_plataformas"]]
    out = []
    for g in d:
        plats = g.get("platforms", "")
        if filtro and not any(p in plats.lower() for p in filtro):
            continue
        titulo = re.sub(r"\s*\(.*?\)|\s*Giveaway$", "", g["title"]).strip()
        fim = None
        if g.get("end_date") not in (None, "", "N/A"):
            fim = iso_ts(g["end_date"].replace(" ", "T"))
        worth = g.get("worth", "")
        out.append(item(f"gp:{g['id']}", "gratis", titulo, plats, g["open_giveaway_url"],
                        g.get("image", ""), "Gratis", worth if worth != "N/A" else "",
                        100, fim=fim))
    return out


def fonte_steam():
    vence = {}
    try:  # a vitrine traz a data de fim de alguns descontos
        f = get_json("https://store.steampowered.com/api/featuredcategories?cc=br&l=brazilian")
        for it in f.get("specials", {}).get("items", []):
            if it.get("discount_expiration"):
                vence[str(it["id"])] = it["discount_expiration"]
    except Exception:
        log_erro("steam vitrine")

    out = []
    for pag in range(CONFIG["steam_paginas"]):
        d = get_json("https://store.steampowered.com/search/results/?specials=1&infinite=1"
                     f"&count=100&start={pag * 100}&cc=br&l=english&category1=998&filter=topsellers")
        for bloco in d["results_html"].split("<a href=")[1:]:
            r = ler_resultado_steam(bloco)
            if not (r["appid"] and r["titulo"] and r["desc"] and r["final"]):
                continue
            desc, final, appid = int(r["desc"]), int(r["final"]), r["appid"]
            if desc < CONFIG["steam_desconto_min"] or (r["nota"] or 0) < CONFIG["steam_nota_min"]:
                continue
            out.append(item(f"steam:{appid}", "promo", html.unescape(r["titulo"]), "Steam",
                            f"https://store.steampowered.com/app/{appid}",
                            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/capsule_231x87.jpg",
                            brl(final), r["original"], desc, r["nota"], fim=vence.get(appid),
                            avaliacoes=r["avaliacoes"], lancamento=r["lancamento"]))
            out[-1]["_centavos"], out[-1]["valor"] = final, final / 100
            out[-1]["img2"] = capa_steam(appid)
            if desc < 100:
                out[-1]["economia"] = brl(round(final * desc / (100 - desc)))
    return out


def fonte_cheapshark():
    lojas = {s["storeID"]: s["storeName"] for s in get_json("https://www.cheapshark.com/api/1.0/stores")}
    deals = get_json("https://www.cheapshark.com/api/1.0/deals?onSale=1&pageSize=60"
                     f"&lowerPrice=0&steamRating={CONFIG['cheapshark_nota_min']}")
    escolhidos = []
    for d in deals:
        if d["storeID"] in ("1", "25"):  # Steam e Epic ja vem em reais pelas outras fontes
            continue
        if float(d["savings"]) < CONFIG["cheapshark_desconto_min"]:
            continue
        escolhidos.append(d)

    menores = {}
    ids = list({d["gameID"] for d in escolhidos})
    for i in range(0, len(ids), 25):
        try:
            for gid, info in get_json("https://www.cheapshark.com/api/1.0/games?ids=" + ",".join(ids[i:i + 25])).items():
                menores[gid] = float(info.get("cheapestPriceEver", {}).get("price", 999))
        except Exception:
            log_erro("cheapshark historico")

    out = []
    for d in escolhidos:
        preco = float(d["salePrice"])
        tipo = "gratis" if preco == 0 else "promo"
        out.append(item(f"cs:{d['dealID']}", tipo, d["title"], lojas.get(d["storeID"], "Loja"),
                        f"https://www.cheapshark.com/redirect?dealID={d['dealID']}", d.get("thumb", ""),
                        "Gratis" if tipo == "gratis" else f"US$ {preco:.2f}", f"US$ {d['normalPrice']}",
                        round(float(d["savings"])), int(d.get("steamRatingPercent") or 0) or None,
                        menor=preco <= menores.get(d["gameID"], -1),
                        avaliacoes=int(d.get("steamRatingCount") or 0),
                        lancamento=d.get("releaseDate") or None, meta=int(d.get("metacriticScore") or 0)))
        out[-1]["valor"] = preco * CONFIG["cotacao_dolar"]
        out[-1]["economia"] = f"US$ {float(d['normalPrice']) - preco:.2f}"
        if d.get("steamAppID"):
            out[-1]["img2"] = capa_steam(d["steamAppID"])
    return out


# ---------------------------------------------------------------- fama
def normalizar(titulo):
    return re.sub(r"[^a-z0-9]", "", titulo.lower())


def enriquecer_fama(itens, estado):
    """Jogos gratis nao trazem numero de avaliacoes: busca o titulo na Steam (com cache de 30 dias)."""
    cache = estado.setdefault("fama", {})
    buscas = 0
    for it in itens:
        if it["avaliacoes"] is not None:
            continue
        n = normalizar(it["titulo"])
        c = cache.get(n)
        if (not c or AGORA - c["t"] > 30 * 86400) and buscas < CONFIG["famoso_buscas_por_vez"]:
            buscas += 1
            c = {"avaliacoes": 0, "lancamento": None, "t": AGORA}
            time.sleep(2)  # a Steam bloqueia consultas muito seguidas
            try:
                d = get_json("https://store.steampowered.com/search/results/?infinite=1&count=3&cc=br"
                             "&l=english&category1=998&term=" + urllib.parse.quote(it["titulo"]))
                for bloco in d["results_html"].split("<a href=")[1:]:
                    r = ler_resultado_steam(bloco)
                    if r["titulo"] and normalizar(html.unescape(r["titulo"])) == n:
                        c.update(avaliacoes=r["avaliacoes"], lancamento=r["lancamento"])
                        break
                cache[n] = c
            except urllib.error.HTTPError as ex:
                c = None
                if ex.code == 429:  # bloqueado: tenta o resto na proxima execucao
                    buscas = CONFIG["famoso_buscas_por_vez"]
                else:
                    log_erro(f"fama {it['titulo']}")
            except Exception:
                c = None
                log_erro(f"fama {it['titulo']}")
        if c:
            it["avaliacoes"], it["lancamento"] = c["avaliacoes"], c["lancamento"]
    limite = AGORA - 60 * 86400
    estado["fama"] = {k: v for k, v in cache.items() if v["t"] > limite}


def classificar_fama(it):
    """Retorna 'famoso', 'lancamento' ou None."""
    if it.get("fama_fixa"):  # veio de uma lista de populares da loja
        return it["fama_fixa"]
    av = it["avaliacoes"] or 0
    if it["lancamento"] and AGORA - it["lancamento"] < CONFIG["lancamento_dias"] * 86400 \
            and av >= CONFIG["lancamento_avaliacoes"]:
        return "lancamento"
    if av >= CONFIG["famoso_avaliacoes"] or (it["meta"] >= CONFIG["famoso_metacritic"]
                                              and av >= CONFIG["lancamento_avaliacoes"]):
        return "famoso"
    return None


def em_destaque(it):
    if not it.get("fama"):
        return False
    return it["tipo"] != "promo" or it["desconto"] >= CONFIG["famoso_desconto_qualquer_preco"] or (
        it["desconto"] >= CONFIG["famoso_desconto_min"] and it.get("valor", 999) <= CONFIG["famoso_preco_max"])


# ---------------------------------------------------------------- estado
def carregar_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return {"vistos": {}, "precos": {}}


def processar(itens, estado):
    vistos, precos = estado["vistos"], estado["precos"]
    primeira_vez = not vistos
    avisar = []
    for it in itens:
        k = it["chave"]
        novo = k not in vistos
        vistos[k] = [vistos[k][0] if not novo else AGORA, AGORA]
        it["novo"] = AGORA - vistos[k][0] < 86400

        if "_centavos" in it:  # historico de preco da Steam
            c, h = it.pop("_centavos"), precos.get(k)
            if h is None:
                precos[k] = {"min": c, "desde": AGORA}
            else:
                if c < h["min"]:
                    it["novo_menor"] = True
                    h["min"] = c
                it["menor"] = c <= h["min"] and AGORA - h["desde"] > 3 * 86400

        it["fama"] = classificar_fama(it)
        if primeira_vez:
            continue
        if novo and em_destaque(it):
            avisar.append(it)
        elif novo and it["tipo"] in ("gratis", "embreve"):
            avisar.append(it)
        elif novo and it["desconto"] >= CONFIG["avisar_desconto_min"]:
            avisar.append(it)
        elif it["novo_menor"] and CONFIG["avisar_menor_preco"]:
            avisar.append(it)

    limite = AGORA - 30 * 86400  # esquece o que sumiu ha mais de 30 dias
    estado["vistos"] = {k: v for k, v in vistos.items() if v[1] > limite}
    return avisar, primeira_vez


def deduplicar(itens):
    """Mesmo jogo gratis em duas fontes: fica o primeiro (Epic vem antes)."""
    vistos, out = set(), []
    for it in itens:
        if it["tipo"] != "promo":
            n = (it["tipo"], re.sub(r"[^a-z0-9]", "", it["titulo"].lower()))
            if n in vistos:
                continue
            vistos.add(n)
        out.append(it)
    return out


# ---------------------------------------------------------------- aviso
def notificar(titulo, linhas, img_url=None):
    if os.name != "nt":  # aviso do Windows so existe no PC
        return
    esc = lambda s: html.escape(s, quote=True)
    textos = "".join(f"<text>{esc(t)}</text>" for t in [titulo] + linhas[:2])
    imagem = ""
    if img_url:  # o Windows so mostra imagem de arquivo local: baixa a capa antes
        try:
            arq = PASTA / "aviso.jpg"
            with urllib.request.urlopen(urllib.request.Request(img_url, headers=UA), timeout=15) as r:
                arq.write_bytes(r.read())
            imagem = f'<image placement="hero" src="{esc(str(arq))}"/>'
        except Exception:
            pass
    xml = (f'<toast activationType="protocol" launch="{esc(PAGINA.as_uri())}">'
           f'<visual><binding template="ToastGeneric">{imagem}{textos}</binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Default"/></toast>')
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(xml.encode()).decode()}')));"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show((New-Object Windows.UI.Notifications.ToastNotification $x))"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                    base64.b64encode(ps.encode("utf-16-le")).decode()],
                   creationflags=0x08000000, timeout=30)  # sem janela


TELEGRAM = PASTA / "telegram.json"  # {"token": "...", "chat_id": ...} - fica so no seu PC


def telegram_api(token, metodo, dados=None):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}",
                                 data=json.dumps(dados).encode() if dados else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["result"]


def enviar_telegram(avisar):
    if os.environ.get("TELEGRAM_TOKEN"):  # na nuvem: vem do cofre secreto do GitHub
        cfg = {"token": os.environ["TELEGRAM_TOKEN"], "chat_id": os.environ.get("TELEGRAM_CHAT_ID")}
    elif not CONFIG["telegram_pelo_pc"]:
        return  # a nuvem ja manda; evita mensagem repetida
    else:
        try:
            cfg = json.loads(TELEGRAM.read_text(encoding="utf-8"))
        except Exception:
            return  # Telegram nao configurado
    token = cfg.get("token")
    if not token:
        return
    if not cfg.get("chat_id"):  # descobre o chat pela mensagem que voce mandou pro robo
        msgs = [u["message"] for u in telegram_api(token, "getUpdates") if "message" in u]
        if not msgs:
            raise RuntimeError("Mande uma mensagem pro seu robo no Telegram antes")
        cfg["chat_id"] = msgs[-1]["chat"]["id"]
        TELEGRAM.write_text(json.dumps(cfg), encoding="utf-8")
    chat = cfg["chat_id"]
    ordem = ordenar_avisos(avisar)
    fotos, resto = ordem[:CONFIG["telegram_fotos"]], ordem[CONFIG["telegram_fotos"]:]

    for i in fotos:  # destaques: um cartao com foto + botao pra loja
        botao = {"inline_keyboard": [[{"text": "\U0001F6D2 Ver na loja", "url": i["url"]}]]}
        try:
            telegram_api(token, "sendPhoto", {"chat_id": chat, "photo": i["img2"] or i["img"],
                                              "caption": legenda_telegram(i)[:1024],
                                              "parse_mode": "HTML", "reply_markup": botao})
        except Exception:  # imagem recusada: manda so o texto
            telegram_api(token, "sendMessage", {"chat_id": chat, "text": legenda_telegram(i),
                                                "parse_mode": "HTML", "reply_markup": botao,
                                                "disable_web_page_preview": True})
    if resto:
        dados = {"chat_id": chat, "text": resumo_telegram(resto, len(avisar))[:4000],
                 "parse_mode": "HTML", "disable_web_page_preview": True}
        if os.environ.get("PAGINA_URL"):
            dados["reply_markup"] = {"inline_keyboard": [[{"text": "\U0001F310 Abrir a central", "url": os.environ["PAGINA_URL"]}]]}
        telegram_api(token, "sendMessage", dados)


def ordenar_avisos(avisar):
    """Famosos primeiro, depois gratis, gratis em breve e promocoes (maior desconto antes)."""
    peso = {"gratis": 1, "embreve": 2, "promo": 3}
    return sorted(avisar, key=lambda i: (not em_destaque(i), peso[i["tipo"]], -i["desconto"], -(i["avaliacoes"] or 0)))


def rotulo(i):
    famoso = em_destaque(i)
    if i["tipo"] == "gratis":
        return ("⭐", "JOGO FAMOSO DE GRACA!") if famoso else ("\U0001F193", "GRATIS PRA RESGATAR")
    if i["tipo"] == "embreve":
        return "⏳", "VAI FICAR GRATIS"
    if famoso:
        return ("\U0001F680", "LANCAMENTO EM OFERTA") if i.get("fama") == "lancamento" else ("⭐", "JOGO FAMOSO EM OFERTA")
    if i["novo_menor"]:
        return "\U0001F4C9", "MENOR PRECO JA VISTO"
    return "\U0001F4B8", "PROMOCAO"


def legenda_telegram(i):
    e = lambda s: html.escape(str(s), quote=False)
    icone, titulo = rotulo(i)
    l = [f"{icone} <b>{titulo}</b>", "", f"\U0001F3AE <b>{e(i['titulo'])}</b>", f"\U0001F3EA {e(i['loja'])}", ""]
    if i["tipo"] == "promo":
        antes = f"<s>{e(i['original'])}</s>  ➜  " if i["original"] else ""
        l.append(f"\U0001F4B0 {antes}<b>{e(i['preco'])}</b>   (-{i['desconto']}%)")
        if i["economia"]:
            l.append(f"\U0001F4B5 Economia de {e(i['economia'])}")
    elif i["tipo"] == "embreve" and i["inicio"]:
        l.append(f"\U0001F4C5 Fica gratis em {data_br(i['inicio'])}")
    else:
        l.append("\U0001F4B0 <b>GRATIS</b>" + (f"  (vale {e(i['original'])})" if i["original"] else ""))
    if i["fim"]:
        l.append(f"⏰ Termina {data_br(i['fim'])}  •  {tempo_restante(i['fim'])}")
    if i["nota"] or i["avaliacoes"]:
        av = i["avaliacoes"] or 0
        qtd = f"{av / 1000:.0f} mil avaliacoes" if av >= 1000 else (f"{av} avaliacoes" if av else "")
        l.append("\U0001F44D " + " • ".join(x for x in (f"{i['nota']}% positivas" if i["nota"] else "", qtd) if x))
    return "\n".join(l)


def resumo_telegram(itens, total):
    e = lambda s: html.escape(str(s), quote=False)
    blocos = [("⭐ FAMOSOS", [i for i in itens if em_destaque(i)]),
              ("\U0001F193 GRATIS AGORA", [i for i in itens if i["tipo"] == "gratis" and not em_destaque(i)]),
              ("⏳ GRATIS EM BREVE", [i for i in itens if i["tipo"] == "embreve" and not em_destaque(i)]),
              ("\U0001F4B8 PROMOCOES", [i for i in itens if i["tipo"] == "promo" and not em_destaque(i)])]
    partes = [f"\U0001F4CB <b>Resumo da varredura</b>\n{total} novidades • {datetime.now():%d/%m %H:%M}"]
    for nome, lista in blocos:
        if not lista:
            continue
        linhas = []
        for i in lista[:8]:
            if i["tipo"] == "promo":
                info = f"-{i['desconto']}% • <b>{e(i['preco'])}</b>"
            elif i["tipo"] == "embreve" and i["inicio"]:
                info = f"a partir de {datetime.fromtimestamp(i['inicio']):%d/%m}"
            else:
                info = "<b>gratis</b>"
            fim = tempo_restante(i["fim"])
            linhas.append(f'▫️ <a href="{html.escape(i["url"])}">{e(i["titulo"])}</a>\n'
                          f'      {info} • {e(i["loja"])}{" • " + fim if fim else ""}')
        if len(lista) > 8:
            linhas.append(f"      <i>+ {len(lista) - 8} na central</i>")
        partes.append(f"<b>{nome}</b> ({len(lista)})\n" + "\n".join(linhas))
    return "\n\n➖➖➖➖➖\n\n".join(partes)


def montar_aviso(avisar, primeira_vez, itens):
    if primeira_vez:
        g = sum(i["tipo"] == "gratis" for i in itens)
        p = sum(i["tipo"] == "promo" for i in itens)
        return "Central de Promocoes pronta", [f"{g} jogos gratis e {p} promocoes agora.", "Clique pra abrir a central."]
    if not avisar:
        return None
    gratis = [i for i in avisar if i["tipo"] in ("gratis", "embreve")]
    promos = [i for i in avisar if i["tipo"] == "promo"]
    famosos = [i for i in avisar if em_destaque(i)]
    if famosos:  # jogo famoso ganha o aviso principal
        famosos.sort(key=lambda i: -(i["avaliacoes"] or 0))
        linha = " | ".join(f"{i['titulo']} ({'gratis' if i['tipo'] != 'promo' else str(i['desconto']) + '%'})" for i in famosos[:3])
        extra = len(avisar) - len(famosos)
        return (f"Jogo famoso em oferta! ({len(famosos)})",
                [linha, f"+{extra} outras novidades. Clique pra ver." if extra else "Clique pra ver."],
                famosos[0]["img2"] or famosos[0]["img"])
    partes = []
    if gratis:
        partes.append(f"{len(gratis)} gratis")
    if promos:
        partes.append(f"{len(promos)} promo{'s' if len(promos) > 1 else ''}")
    destaque = (gratis + promos)[:3]
    linha = " | ".join(f"{i['titulo']} ({'gratis' if i['tipo'] != 'promo' else str(i['desconto']) + '%'})" for i in destaque)
    return "Novidades: " + " e ".join(partes), [linha, "Clique pra ver tudo."], destaque[0]["img2"] or destaque[0]["img"]


# ---------------------------------------------------------------- pagina
def tempo_restante(ts):
    if not ts:
        return ""
    s = ts - AGORA
    if s <= 0:
        return "encerrando"
    if s < 86400:
        return f"acaba em {int(s // 3600)}h"
    return f"acaba em {int(s // 86400)}d"


def card(it):
    e = lambda s: html.escape(str(s))
    tags = []
    if it.get("fama") == "lancamento":
        tags.append('<span class="tag lanc">LANCAMENTO</span>')
    elif it.get("fama"):
        tags.append('<span class="tag fama">FAMOSO</span>')
    if it.get("novo"):
        tags.append('<span class="tag novo">NOVO</span>')
    if it["novo_menor"]:
        tags.append('<span class="tag menor">NOVO MENOR PRECO</span>')
    elif it["menor"]:
        tags.append('<span class="tag menor">MENOR PRECO</span>')
    if it["fim"] and it["fim"] - AGORA < CONFIG["horas_acaba_logo"] * 3600:
        tags.append('<span class="tag logo">ACABA LOGO</span>')
    if it["tipo"] == "embreve" and it["inicio"]:
        quando = f"gratis a partir de {datetime.fromtimestamp(it['inicio']):%d/%m %H:%M}"
    else:
        quando = tempo_restante(it["fim"])
    desc = f'<span class="desc">-{it["desconto"]}%</span>' if it["tipo"] == "promo" else ""
    orig = f'<s>{e(it["original"])}</s>' if it["original"] else ""
    av = it["avaliacoes"] or 0
    qtd = f'{av / 1000:.0f} mil aval.' if av >= 1000 else (f"{av} aval." if av else "")
    nota = " · ".join(x for x in (f'{it["nota"]}% positivas' if it["nota"] else "", qtd) if x)
    nota = f'<span class="nota">{nota}</span>' if nota else ""
    flags = [it.get("fama") and "famoso", it.get("fama") == "lancamento" and "lancamento",
             it.get("novo") and "novo", (it["menor"] or it["novo_menor"]) and "menor",
             it["fim"] and it["fim"] - AGORA < CONFIG["horas_acaba_logo"] * 3600 and "logo"]
    dados = (f'data-busca="{e((it["titulo"] + " " + it["loja"]).lower())}" data-tipo="{it["tipo"]}" '
             f'data-lojas="{e(" ".join(chaves_loja(it)))}" data-flags="{" ".join(f for f in flags if f)}" '
             f'data-desc="{it["desconto"]}" data-valor="{0 if it["tipo"] != "promo" else round(it.get("valor", 0), 2)}" '
             f'data-av="{av}"')
    return (f'<a class="card" href="{e(it["url"])}" target="_blank" {dados}>'
            f'<div class="img" style="--img:url(\'{e(it["img"])}\');--img2:url(\'{e(it["img2"])}\')">'
            f'<div class="tags">{"".join(tags)}</div><div class="selo">{desc}<strong>{e(it["preco"])}</strong></div></div>'
            f'<div class="info"><b>{e(it["titulo"])}</b><small>{e(it["loja"])}</small>'
            f'<div class="preco">{desc}{orig}<strong>{e(it["preco"])}</strong></div>'
            f'<div class="meta">{nota}<span>{e(quando)}</span></div>{detalhes(it)}</div></a>')


DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def data_br(ts, hora=True):
    d = datetime.fromtimestamp(ts)
    return f"{DIAS[d.weekday()]}, {d:%d/%m/%Y}" + (f" as {d:%H:%M}" if hora else "")


def detalhes(it):
    """Bloco extra que so aparece na visao detalhada."""
    e = lambda s: html.escape(str(s))
    linhas = []
    if it["tipo"] == "embreve" and it["inicio"]:
        linhas.append(("Fica gratis", data_br(it["inicio"]) + (f" ate {data_br(it['fim'])}" if it["fim"] else "")))
    elif it["fim"]:
        linhas.append(("Termina", f'{data_br(it["fim"])} <em class="cd" data-fim="{int(it["fim"])}"></em>'))
    else:
        linhas.append(("Termina", "<span class='apagado'>data nao informada pela loja</span>"))
    if it["economia"]:
        linhas.append(("Economia", f'<b class="verde">{e(it["economia"])}</b>' + (f" (de {e(it['original'])})" if it["original"] else "")))
    elif it["tipo"] != "promo" and it["original"]:
        linhas.append(("Valor normal", e(it["original"])))
    if it["nota"] or it["avaliacoes"]:
        av = f'{it["avaliacoes"]:,}'.replace(",", ".") if it["avaliacoes"] else ""
        txt = f'{it["nota"]}% positivas' if it["nota"] else ""
        linhas.append(("Avaliacoes", " de ".join(x for x in (txt, av) if x) + (" na Steam" if av else "")))
    if it["meta"]:
        linhas.append(("Metacritic", f'<span class="meta-nota">{it["meta"]}</span>'))
    if it["lancamento"]:
        linhas.append(("Lancamento", data_br(it["lancamento"], hora=False)))
    linhas.append(("Onde", e(it["loja"])))
    corpo = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in linhas)
    return f'<dl class="det">{corpo}</dl><span class="abrir">Ver na loja &rarr;</span>'


GENERICOS = {"pc", "drm", "drm-free"}


def chaves_loja(it):
    """'Epic Games Store, Steam' -> ['epic', 'steam'] (primeira palavra de cada loja)."""
    out = []
    for p in it["loja"].split(","):
        k = p.strip().split(" ")[0].lower()
        if k and k not in GENERICOS and k not in out:
            out.append(k)
    return out


def botoes_loja(itens):
    nomes, cont = {}, {}
    for it in itens:
        for p in it["loja"].split(","):
            p = p.strip()
            k = p.split(" ")[0].lower()
            if k and k not in GENERICOS:
                nomes.setdefault(k, p.replace(" Store", ""))
                cont[k] = cont.get(k, 0) + 1
    top = sorted(cont, key=lambda k: -cont[k])[:12]
    return "".join(f'<button data-loja="{html.escape(k)}">{html.escape(nomes[k])} <i>{cont[k]}</i></button>' for k in top)


def secao(titulo, itens):
    if not itens:
        return ""
    return f'<section><h2>{titulo} <span>{len(itens)}</span></h2><div class="grade">{"".join(card(i) for i in itens)}</div></section>'


def gerar_pagina(itens, erros):
    peso = lambda i: (not i.get("novo"), not (i["menor"] or i["novo_menor"]), -i["desconto"])
    gratis = sorted([i for i in itens if i["tipo"] == "gratis"], key=lambda i: (not i.get("novo"), i["fim"] or 9e12))
    embreve = sorted([i for i in itens if i["tipo"] == "embreve"], key=lambda i: i["inicio"] or 0)
    promos = [i for i in itens if i["tipo"] == "promo"]
    destaques = sorted([i for i in promos if i["menor"] or i["novo_menor"] or i["desconto"] >= CONFIG["avisar_desconto_min"]], key=peso)
    steam = sorted([i for i in promos if i["loja"] == "Steam"], key=peso)
    epic = sorted([i for i in promos if i["loja"] == "Epic Games"], key=peso)
    outras = sorted([i for i in promos if i["loja"] not in ("Steam", "Epic Games")], key=peso)
    famosos = sorted([i for i in itens if em_destaque(i)],
                     key=lambda i: (i["tipo"] == "promo", -(i["avaliacoes"] or 0)))
    aviso_erro = f'<p class="erro">Falhou: {", ".join(erros)} (veja erros.log)</p>' if erros else ""
    corpo = (secao("&#11088; Famosos e lancamentos gratis ou baratos", famosos) +
             secao("Gratis agora", gratis) + secao("Gratis em breve", embreve) +
             secao("Destaques (menor preco ou desconto alto)", destaques) +
             secao("Steam (R$)", steam) + secao("Epic Games (R$)", epic) +secao("Outras lojas (US$)", outras))
    PAGINA.write_text(f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Central de Promocoes</title>
<style>
body{{margin:0;font-family:Segoe UI,system-ui,sans-serif;background:#0b0e14;color:#e6e9ef}}
/* fundo: aurora animada + grade sutil (fica atras de tudo) */
body::before{{content:"";position:fixed;inset:-20%;z-index:-1;pointer-events:none;will-change:transform;
  background:radial-gradient(38% 32% at 18% 22%,#2563eb8c,transparent 70%),radial-gradient(34% 30% at 82% 18%,#9333ea80,transparent 70%),
  radial-gradient(40% 38% at 62% 82%,#16a34a66,transparent 70%),radial-gradient(28% 28% at 8% 88%,#ca8a0459,transparent 70%),
  radial-gradient(26% 24% at 45% 50%,#0891b24d,transparent 70%);filter:blur(40px);animation:aurora 45s ease-in-out infinite alternate}}
@keyframes aurora{{0%{{transform:translate(0,0) rotate(0) scale(1)}}50%{{transform:translate(5%,-4%) rotate(10deg) scale(1.08)}}100%{{transform:translate(-5%,4%) rotate(-8deg) scale(1)}}}}
body::after{{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background-image:linear-gradient(#ffffff12 1px,transparent 1px),linear-gradient(90deg,#ffffff12 1px,transparent 1px);background-size:44px 44px;
  -webkit-mask-image:radial-gradient(ellipse 90% 70% at 50% 0%,#000 25%,transparent 75%);mask-image:radial-gradient(ellipse 90% 70% at 50% 0%,#000 25%,transparent 75%)}}
@media (prefers-reduced-motion:reduce){{body::before{{animation:none}}}}
header{{position:sticky;top:0;z-index:2;background:#0e121acc;backdrop-filter:blur(6px);padding:14px 24px;display:flex;gap:16px;align-items:center;border-bottom:1px solid #232a36}}
header h1{{font-size:20px;margin:0}} header small{{color:#8a93a6}}
input{{margin-left:auto;background:#0f1218;border:1px solid #2c3442;color:#e6e9ef;padding:8px 12px;border-radius:8px;width:260px}}
section{{padding:8px 24px}} h2{{font-size:17px;margin:18px 0 10px}} h2 span{{color:#8a93a6;font-weight:400}}
.grade{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}}
.card{{background:#171c26;border:1px solid #232a36;border-radius:10px;overflow:hidden;color:inherit;text-decoration:none;transition:.15s}}
.card:hover{{transform:translateY(-2px);border-color:#3d4a60}}
.img{{height:108px;position:relative;overflow:hidden;background:#222}}
.img::before{{content:"";position:absolute;inset:0;background:var(--img) center/cover;transition:transform .5s}}
.tags{{position:relative;z-index:1;display:flex;gap:4px;flex-wrap:wrap;padding:6px}}
.selo,.det,.abrir{{display:none}}
#visao{{background:#171c26;border:1px solid #2c3442;color:#e6e9ef;padding:8px 14px;border-radius:8px;cursor:pointer}}
#visao:hover{{border-color:#4b5870}}
/* ---- visao detalhada ---- */
.v2 .grade{{grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:22px}}
.v2 .card{{border-radius:16px;background:linear-gradient(180deg,#1a2030,#141924);border-color:#262e3d;box-shadow:0 8px 24px #0006;display:flex;flex-direction:column}}
.v2 .card:hover{{transform:translateY(-4px);border-color:#4f6aa8;box-shadow:0 14px 34px #000a}}
.v2 .img{{height:190px}}
.v2 .img::before{{background-image:var(--img2),var(--img)}}
.v2 .card:hover .img::before{{transform:scale(1.06)}}
.v2 .img::after{{content:"";position:absolute;inset:0;background:linear-gradient(180deg,#0000 45%,#0b0e14ee)}}
.v2 .tag{{font-size:11px;padding:4px 8px;border-radius:6px}}
.v2 .selo{{display:flex;position:absolute;z-index:1;left:14px;bottom:12px;gap:8px;align-items:center}}
.v2 .selo strong{{font-size:22px;color:#bef264;text-shadow:0 2px 8px #000}} .v2 .selo .desc{{font-size:14px;padding:3px 8px}}
.v2 .info{{padding:14px 16px 16px;gap:6px;flex:1}}
.v2 .info b{{font-size:17px;white-space:normal}} .v2 .preco,.v2 .meta{{display:none}}
.v2 .det{{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;margin:8px 0 4px;font-size:13px;padding-top:10px;border-top:1px solid #262e3d}}
.v2 dt{{color:#7d879b}} .v2 dd{{margin:0;color:#d6dbe6}}
.cd{{font-style:normal;color:#fbbf24;margin-left:4px}} .verde{{color:#a3e635}} .apagado{{color:#6b7385}}
.meta-nota{{background:#166534;color:#dcfce7;padding:1px 7px;border-radius:4px;font-weight:700}}
.v2 .abrir{{display:block;margin-top:auto;padding-top:10px;color:#93b4ff;font-size:13px;font-weight:600}}
.tag{{font-size:10px;font-weight:700;padding:3px 6px;border-radius:4px;background:#000c}}
.fama{{background:#ca8a04}} .lanc{{background:#9333ea}} .novo{{background:#2563eb}} .menor{{background:#16a34a}} .logo{{background:#dc2626}}
.info{{padding:10px 12px;display:flex;flex-direction:column;gap:4px}}
.info b{{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .info small{{color:#8a93a6;font-size:12px}}
.preco{{display:flex;gap:8px;align-items:center}} .preco s{{color:#6b7385;font-size:12px}} .preco strong{{color:#a3e635}}
.desc{{background:#4c6b22;color:#d9f99d;font-weight:700;font-size:12px;padding:2px 6px;border-radius:4px}}
.meta{{display:flex;justify-content:space-between;color:#8a93a6;font-size:11px}}
.erro{{color:#f87171;padding:0 24px}}
.filtros{{position:sticky;top:61px;z-index:1;background:#0b0e14b8;backdrop-filter:blur(10px);padding:10px 24px;display:flex;flex-direction:column;gap:8px;border-bottom:1px solid #232a36}}
.linha{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}} .linha>span{{color:#8a93a6;font-size:12px;width:52px}}
.filtros button{{background:#171c26;border:1px solid #2c3442;color:#cfd5e1;padding:5px 11px;border-radius:999px;cursor:pointer;font-size:13px}}
.filtros button:hover{{border-color:#4b5870}} .filtros button.on{{background:#2563eb;border-color:#2563eb;color:#fff}}
.filtros button i{{font-style:normal;color:#8a93a6;font-size:11px}} .filtros button.on i{{color:#dbeafe}}
select{{background:#171c26;border:1px solid #2c3442;color:#cfd5e1;padding:5px 8px;border-radius:8px}}
#vazio{{display:none;color:#8a93a6;padding:30px 24px}}
</style></head><body>
<header><h1>Central de Promocoes</h1><small>atualizado {datetime.now():%d/%m/%Y %H:%M}</small>
<input id="q" placeholder="Buscar jogo ou loja..." autofocus><button id="visao"></button></header>
<div class="filtros">
<div class="linha" id="ftipo"><span>Mostrar</span>
<button class="on" data-f="">Tudo</button><button data-f="t:gratis">Gratis agora</button><button data-f="t:embreve">Gratis em breve</button>
<button data-f="t:promo">Promocoes</button><button data-f="f:famoso">Famosos</button><button data-f="f:lancamento">Lancamentos</button>
<button data-f="f:menor">Menor preco</button><button data-f="f:logo">Acaba logo</button><button data-f="f:novo">Novos</button></div>
<div class="linha" id="floja"><span>Loja</span><button class="on" data-loja="">Todas</button>{botoes_loja(itens)}</div>
<div class="linha"><span>Ordem</span><select id="ordem"><option value="">Padrao</option><option value="desc">Maior desconto</option>
<option value="valor">Menor preco</option><option value="av">Mais populares</option></select>
<span id="total" style="width:auto;margin-left:8px"></span></div>
</div>
{aviso_erro}{corpo}<p id="vazio">Nada encontrado com esses filtros.</p>
<script>
let fTipo='', fLoja='';
document.querySelectorAll('.grade').forEach(g=>[...g.children].forEach((c,i)=>c.dataset.i=i));
function aplicar(){{
  const t=q.value.toLowerCase(); const unicos=new Set(); let n=0;
  document.querySelectorAll('section').forEach(s=>{{
    let vis=0;
    s.querySelectorAll('.card').forEach(c=>{{
      const d=c.dataset; let ok=d.busca.includes(t);
      if(ok&&fTipo){{const [k,v]=fTipo.split(':');ok=k==='t'?d.tipo===v:d.flags.split(' ').includes(v)}}
      if(ok&&fLoja)ok=d.lojas.split(' ').includes(fLoja);
      c.style.display=ok?'':'none'; if(ok){{vis++;unicos.add(c.href)}}
    }});
    s.style.display=vis?'':'none'; n+=vis;
  }});
  n=unicos.size;
  total.textContent=n+' resultado'+(n===1?'':'s'); vazio.style.display=n?'none':'block';
}}
function ordenar(){{
  const o=ordem.value;
  document.querySelectorAll('.grade').forEach(g=>{{
    [...g.children].sort((a,b)=>o==='desc'?b.dataset.desc-a.dataset.desc:o==='valor'?a.dataset.valor-b.dataset.valor
      :o==='av'?b.dataset.av-a.dataset.av:a.dataset.i-b.dataset.i).forEach(c=>g.appendChild(c));
  }});
}}
function grupo(id,set){{document.getElementById(id).addEventListener('click',e=>{{
  const b=e.target.closest('button'); if(!b)return;
  document.querySelectorAll('#'+id+' button').forEach(x=>x.classList.remove('on')); b.classList.add('on'); set(b); aplicar();
}})}}
grupo('ftipo',b=>fTipo=b.dataset.f); grupo('floja',b=>fLoja=b.dataset.loja);
q.oninput=aplicar; ordem.onchange=ordenar; aplicar();
function setVisao(v2){{
  document.body.classList.toggle('v2',v2); localStorage.setItem('visao',v2?'2':'1');
  visao.textContent=v2?'▦ Visao compacta':'▣ Visao detalhada';
}}
visao.onclick=()=>setVisao(!document.body.classList.contains('v2'));
setVisao(localStorage.getItem('visao')==='2' || location.hash==='#v2');
function contagem(){{
  const agora=Date.now()/1000;
  document.querySelectorAll('.cd').forEach(el=>{{
    const s=el.dataset.fim-agora;
    if(s<=0){{el.textContent='(encerrada)';return}}
    const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
    el.textContent='(faltam '+(d?d+'d '+h+'h':h?h+'h '+m+'min':m+'min')+')';
  }});
}}
contagem(); setInterval(contagem,60000);
</script></body></html>""", encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    itens, erros = [], []
    for nome, fn in (("Epic", fonte_epic), ("Epic promocoes", fonte_epic_promos), ("GamerPower", fonte_gamerpower),
                     ("Steam", fonte_steam), ("CheapShark", fonte_cheapshark)):
        try:
            itens += fn()
        except Exception:
            erros.append(nome)
            log_erro(nome)
    itens = deduplicar(itens)

    estado = carregar_estado()
    try:
        enriquecer_fama(itens, estado)
    except Exception:
        log_erro("fama")
    avisar, primeira_vez = processar(itens, estado)
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(estado, indent=0), encoding="utf-8")
    gerar_pagina(itens, erros)

    aviso = montar_aviso(avisar, primeira_vez, itens)
    if aviso:
        try:
            notificar(*aviso)
        except Exception:
            log_erro("notificacao")
    if avisar:
        try:
            enviar_telegram(avisar)
        except Exception:
            log_erro("telegram")
    print(f"{len(itens)} itens, {len(avisar)} novidades, erros: {erros or 'nenhum'}")
    if "--abrir" in sys.argv:
        webbrowser.open(PAGINA.as_uri())


if __name__ == "__main__":
    main()
