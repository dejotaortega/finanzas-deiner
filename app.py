from flask import Flask, render_template, request, redirect, url_for, session
from babel.numbers import format_currency

from datetime import date, timedelta
import os
import json

import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

app.secret_key = "Deiner88@"  # Puedes cambiarla

# Categorías de gastos e ingresos
CATEGORIAS_GASTO = [
    "Abastecimiento y alimentacion",
    "Ahorro e inversiones",
    "Asuntos familiares y responsabilidad social",
    "Contingencias y gastos miscelaneos",
    "Educacion y formacion",
    "Financiera y gestion de deudas",
    "Gestion de gastos laborales",
    "Infraestructura y servicios del hogar",
    "Negocios y desarrollo empresarial",
    "Recreacion y entretenimiento",
    "Salud y bienestar",
    "Transporte y movilidad",
    "Vestuario y presentacion personal",
]

CATEGORIAS_INGRESO = [
    "Sueldo Deiner",
    "Sueldo Sole",
    "Negocios",
    "Ariadna Babyshop",
    "Otros ingresos",
    "Prestamos",
]


# Función para formatear moneda COP
def formatear_cop(valor):
    if valor is None:
        valor = 0
    try:
        return format_currency(valor, 'COP', locale='es_CO')
    except Exception:
        # Por si llega algo raro, no revienta
        return f"${valor:,.0f}"

def obtener_saldo_inicial_dia(total_cuentas):
    """
    Usa la colección saldos_diarios para guardar y consultar
    el saldo inicial / final de cada día.
    """
    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    saldo_inicial = total_cuentas  # por defecto, si es el primer día

    # Buscar el saldo final de ayer
    doc_ayer = db.collection("saldos_diarios").document(ayer.isoformat()).get()
    if doc_ayer.exists:
        datos_ayer = doc_ayer.to_dict()
        saldo_inicial = datos_ayer.get("saldo_final", total_cuentas)

    # Actualizar / crear el documento de hoy
    db.collection("saldos_diarios").document(hoy.isoformat()).set(
        {
            "fecha": hoy.isoformat(),
            "saldo_inicial": saldo_inicial,
            "saldo_final": total_cuentas,
        },
        merge=True,
    )

    return saldo_inicial

# ---- Inicializar Firebase (Render + local) ----
firebase_cert = os.getenv("FIREBASE_CREDENTIALS")

if not firebase_admin._apps:
    try:
        if firebase_cert:
            # Viene como JSON en una variable de entorno (Render)
            cred_info = json.loads(firebase_cert)
            cred = credentials.Certificate(cred_info)
            firebase_admin.initialize_app(cred)
        elif os.path.exists("finanzas-deiner-firebase-adminsdk-fbsvc-9d70b13e78.json"):
            # Modo local con archivo físico
            cred = credentials.Certificate(
                "finanzas-deiner-firebase-adminsdk-fbsvc-9d70b13e78.json"
            )
            firebase_admin.initialize_app(cred)
        else:
            raise RuntimeError(
                "No encontré ni FIREBASE_CREDENTIALS ni el archivo de credenciales."
            )
    except Exception as e:
        print("ERROR al inicializar Firebase:", e)
        raise

db = firestore.client()
# Referencia al documento donde llevamos el contador de transacciones
contador_trans_ref = db.collection("config").document("transacciones")


@firestore.transactional
def obtener_siguiente_id_transaccion(transaction):
    snapshot = contador_trans_ref.get(transaction=transaction)

    if snapshot.exists:
        actual = snapshot.get("contador") or 0
    else:
        actual = 0

    nuevo = actual + 1
    transaction.update(contador_trans_ref, {"contador": nuevo})
    return nuevo

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        usuario = request.form.get("usuario")
        password = request.form.get("password")

        # ------ CREDENCIALES ------
        if usuario == "deiner" and password == "Deiner88@": 
            session["logged"] = True
            return redirect(url_for("home"))
        else:
            error = "Usuario o contraseña incorrectos"

    return render_template("login.html", error=error)

from functools import wraps

def login_requerido(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_requerido
def home():
    # -------- 1) SALDO GLOBAL ACTUAL --------
    ult_docs = (
        db.collection("transacciones")
        .order_by("id_transaccion", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )

    total_global = 0
    for d in ult_docs:
        total_global = d.to_dict().get("saldo_final", 0)

    # Si no hay transacciones aún → suma de saldos de las cuentas
    if total_global == 0:
        cuentas_docs_tmp = db.collection("cuentas").stream()
        total_global = sum(c.to_dict().get("saldo_inicial", 0) for c in cuentas_docs_tmp)

    # -------- 2) RESUMEN DEL DÍA --------
    hoy = date.today().isoformat()

    trans_hoy = (
        db.collection("transacciones")
        .where("fecha", "==", hoy)
        .stream()
    )

    ingresos_hoy = 0.0
    gastos_hoy = 0.0

    for t in trans_hoy:
        data = t.to_dict()
        valor = float(data.get("valor", 0))
        if valor > 0:
            ingresos_hoy += valor
        else:
            gastos_hoy += abs(valor)

    diferencia_hoy = ingresos_hoy - gastos_hoy

    # -------- 3) RESUMEN DEL MES ACTUAL --------
    mes_actual = hoy[:7]  # YYYY-MM

    trans_mes = db.collection("transacciones").stream()

    ingresos_mes = 0.0
    gastos_mes = 0.0

    for t in trans_mes:
        data = t.to_dict()
        fecha_t = data.get("fecha")
        if not fecha_t:
            continue

        if fecha_t.startswith(mes_actual):
            valor = float(data.get("valor", 0))
            if valor > 0:
                ingresos_mes += valor
            else:
                gastos_mes += abs(valor)

    diferencia_mes = ingresos_mes - gastos_mes

    # -------- 4) DATOS PARA GRÁFICA MENSUAL --------
    resumen_diario, _ = calcular_resumen_diario()

    resumen_mes_dict = {}
    for r in resumen_diario:
        mes = r["fecha"][:7]  # YYYY-MM
        if mes not in resumen_mes_dict:
            resumen_mes_dict[mes] = {"ingresos": 0.0, "gastos": 0.0}
        resumen_mes_dict[mes]["ingresos"] += r["ingresos"]
        resumen_mes_dict[mes]["gastos"] += r["gastos"]

    meses_labels = sorted(resumen_mes_dict.keys())
    ingresos_mes_chart = [resumen_mes_dict[m]["ingresos"] for m in meses_labels]
    gastos_mes_chart = [resumen_mes_dict[m]["gastos"] for m in meses_labels]

    meses_labels_json = json.dumps(meses_labels)
    ingresos_mes_chart_json = json.dumps(ingresos_mes_chart)
    gastos_mes_chart_json = json.dumps(gastos_mes_chart)

    # -------- 5) GASTOS POR CATEGORÍA (MES ACTUAL) --------
    from collections import defaultdict
    gastos_por_categoria = defaultdict(float)

    trans_mes_cat = db.collection("transacciones").stream()
    for t in trans_mes_cat:
        data = t.to_dict()
        fecha_t = data.get("fecha")
        if not fecha_t or not fecha_t.startswith(mes_actual):
            continue

        tipo_t = (data.get("tipo", "") or "").lower()
        if tipo_t != "gasto":
            continue

        categoria = data.get("categoria", "Sin categoría")
        valor = abs(float(data.get("valor", 0)))
        gastos_por_categoria[categoria] += valor

    cat_labels = list(gastos_por_categoria.keys())
    cat_values = list(gastos_por_categoria.values())

    cat_labels_json = json.dumps(cat_labels)
    cat_values_json = json.dumps(cat_values)

    # -------- 6) ÚLTIMAS TRANSACCIONES --------
    ult_trans_docs = (
        db.collection("transacciones")
        .order_by("id_transaccion", direction=firestore.Query.DESCENDING)
        .limit(5)
        .stream()
    )

    ultimas_transacciones = []
    for d in ult_trans_docs:
        data = d.to_dict()
        valor = float(data.get("valor", 0))
        tipo_t = (data.get("tipo", "") or "").lower()
        if tipo_t == "gasto":
            data["valor_mostrado"] = -abs(valor)
        else:
            data["valor_mostrado"] = abs(valor)
        ultimas_transacciones.append(data)

    # -------- 7) SALDOS POR CUENTA PARA EL DASHBOARD --------
    cuentas_docs = db.collection("cuentas").stream()
    cuentas_dashboard = []
    for c in cuentas_docs:
        info = c.to_dict()
        cuentas_dashboard.append({
            "nombre": info.get("nombre", ""),
            "saldo": info.get("saldo_inicial", 0),
        })

    # -------- 8) RENDER --------
    return render_template(
        "home.html",
        total_global=total_global,
        ingresos_hoy=ingresos_hoy,
        gastos_hoy=gastos_hoy,
        diferencia_hoy=diferencia_hoy,
        ingresos_mes=ingresos_mes,
        gastos_mes=gastos_mes,
        diferencia_mes=diferencia_mes,
        meses_labels_json=meses_labels_json,
        ingresos_mes_chart_json=ingresos_mes_chart_json,
        gastos_mes_chart_json=gastos_mes_chart_json,
        cat_labels_json=cat_labels_json,
        cat_values_json=cat_values_json,
        ultimas_transacciones=ultimas_transacciones,
        cuentas_dashboard=cuentas_dashboard,   # 👈 AQUÍ VAN LAS CUENTAS
    )

@app.route("/historicos")
@login_requerido
def historicos():
    return render_template("historicos.html")


@app.route("/cuentas", methods=["GET", "POST"])
@login_requerido
def cuentas():
    error_msg = None

    # Crear cuenta
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        saldo_inicial_str = request.form.get("saldo_inicial", "0").strip()

        try:
            saldo_inicial = float(saldo_inicial_str or 0)
        except ValueError:
            saldo_inicial = 0

        if not nombre:
            error_msg = "El nombre de la cuenta es obligatorio."
        else:
            # Evitar duplicados
            nombre_key = nombre.lower()
            docs = db.collection("cuentas").stream()
            nombres_existentes = [
                (d.to_dict().get("nombre", "").strip().lower())
                for d in docs
            ]

            if nombre_key in nombres_existentes:
                error_msg = "Ya existe una cuenta con ese nombre."
            else:
                db.collection("cuentas").add({
                    "nombre": nombre,
                    "saldo_inicial": saldo_inicial,
                })
                return redirect(url_for("cuentas"))

    # --- Leer todas las cuentas ---
    docs = db.collection("cuentas").stream()
    lista = []
    for d in docs:
        data = d.to_dict()
        data["id"] = d.id
        lista.append(data)

    # Total de cuentas (solo suma de saldos de cada cuenta)
    total_cuentas = sum(c.get("saldo_inicial", 0) for c in lista)

    # 2️⃣ OBTENER SALDO ACTUAL REAL desde la ÚLTIMA transacción
    ult_docs = (
        db.collection("transacciones")
        .order_by("id_transaccion", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )

    saldo_actual_real = total_cuentas  # por defecto si no hay transacciones
    for d in ult_docs:
        saldo_actual_real = d.to_dict().get("saldo_final", total_cuentas)

    # 1️⃣ SALDO INICIAL DEL DÍA (saldos_diarios)
    #    Si es un día nuevo, el saldo inicial del día = saldo_actual_real
    hoy = date.today().isoformat()
    doc_hoy_ref = db.collection("saldos_diarios").document(hoy)
    doc_hoy = doc_hoy_ref.get()

    if doc_hoy.exists:
        # Ya había saldo guardado hoy
        saldo_inicial_dia = doc_hoy.to_dict().get("saldo_inicial", saldo_actual_real)
    else:
        # Día nuevo → el saldo inicial ES el saldo actual global
        saldo_inicial_dia = saldo_actual_real
        doc_hoy_ref.set({
            "fecha": hoy,
            "saldo_inicial": saldo_inicial_dia,
            "saldo_final": saldo_inicial_dia,
        })

    # 👇 OJO: este return va alineado con el if, NO dentro del else
    return render_template(
        "cuentas.html",
        cuentas=lista,
        formatear_cop=formatear_cop,
        total_cuentas=saldo_actual_real,
        saldo_inicial_dia=saldo_inicial_dia,
        error=error_msg,
    )


@app.route("/cuentas/borrar/<id_doc>", methods=["POST"])
def borrar_cuenta(id_doc):
    db.collection("cuentas").document(id_doc).delete()
    return redirect(url_for("cuentas"))


@app.route("/cuentas/editar/<id_doc>", methods=["POST"])
def editar_cuenta(id_doc):
    """
    Actualiza el saldo_inicial (saldo actual) de una cuenta existente.
    Acepta valores con formato tipo $1.234.567,89 o números crudos.
    """
    bruto = request.form.get("saldo_inicial", "0").strip()

    # Quitar símbolo de peso, espacios y separadores de miles
    limpio = (
        bruto.replace("$", "")
        .replace(" ", "")
        .replace("\u00a0", "")  # espacio raro
        .replace(".", "")  # puntos de miles
    )
    # Cambiar coma decimal por punto
    limpio = limpio.replace(",", ".")

    try:
        nuevo_saldo = float(limpio or 0)
    except ValueError:
        nuevo_saldo = 0

    db.collection("cuentas").document(id_doc).update({"saldo_inicial": nuevo_saldo})

    return redirect(url_for("cuentas"))


@app.route("/transacciones", methods=["GET", "POST"])
@login_requerido
def transacciones():
    error = None

    # 1. Determinar la FECHA DE TRABAJO
    if request.method == "POST":
        fecha_trabajo = request.form.get("fecha_trabajo") or date.today().isoformat()
    else:
        fecha_trabajo = request.args.get("fecha") or date.today().isoformat()

    # ------------------- POST: guardar transacción -------------------
    if request.method == "POST":
        # la fecha REAL de la transacción SIEMPRE será la fecha_trabajo
        fecha = fecha_trabajo
        descripcion = request.form.get("descripcion", "").strip()
        valor_str = request.form.get("valor", "0").strip()
        tipo = (request.form.get("tipo", "ingreso") or "").lower()

        cuenta = request.form.get("cuenta", "").strip()
        categoria = request.form.get("categoria", "").strip()

        if not cuenta:
            error = "Debes seleccionar una cuenta."
        else:
            try:
                valor = float(valor_str or 0)
            except ValueError:
                error = "El valor ingresado no es válido."

        if not error:
            # 1. Ajustar signo
            if tipo.lower() == "gasto":
                valor_ajustado = -abs(valor)
            else:
                valor_ajustado = abs(valor)

                       # 2. Saldo en la cuenta (saldo_en_cuenta)
            trans_docs = (
                db.collection("transacciones")
                .where("cuenta", "==", cuenta)
                .order_by("id_transaccion")
                .stream()
            )

            saldo_anterior_cuenta = None
            for t in trans_docs:
                data_t = t.to_dict()
                saldo_anterior_cuenta = data_t.get("saldo_en_cuenta")

            if saldo_anterior_cuenta is None:
                cuenta_docs = (
                    db.collection("cuentas")
                    .where("nombre", "==", cuenta)
                    .limit(1)
                    .stream()
                )
                saldo_anterior_cuenta = 0
                for c in cuenta_docs:
                    datos_c = c.to_dict()
                    saldo_anterior_cuenta = datos_c.get("saldo_inicial", 0)

            saldo_en_cuenta = saldo_anterior_cuenta + valor_ajustado

            # 3. Saldo global (saldo_inicial / saldo_final)
            #    OJO: aquí AÚN NO actualizamos la colección "cuentas"
            #    para no contar dos veces la misma transacción.
            saldo_inicial_global = None

            ult_docs = (
                db.collection("transacciones")
                .order_by("id_transaccion", direction=firestore.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for t in ult_docs:
                saldo_inicial_global = t.to_dict().get("saldo_final")

            if saldo_inicial_global is None:
                # No hay transacciones con saldo_final (versión vieja) →
                # usamos la suma de saldos de cuentas ANTES de aplicar esta transacción.
                cuentas_docs = db.collection("cuentas").stream()
                saldo_inicial_global = sum(
                    c.to_dict().get("saldo_inicial", 0) for c in cuentas_docs
                )

            saldo_final_global = saldo_inicial_global + valor_ajustado

            # 3.b AHORA sí actualizamos el saldo de la cuenta en "cuentas"
            cuentas_query = (
                db.collection("cuentas")
                .where("nombre", "==", cuenta)
                .limit(1)
                .stream()
            )
            for c_doc in cuentas_query:
                c_doc.reference.update({"saldo_inicial": saldo_en_cuenta})


            # 4. Nuevo ID de transacción
            config_ref = db.collection("config").document("transacciones")
            config_doc = config_ref.get()
            contador_actual = 0
            if config_doc.exists:
                data_conf = config_doc.to_dict()
                contador_actual = data_conf.get("contador", 0)

            nuevo_id = contador_actual + 1
            config_ref.set({"contador": nuevo_id}, merge=True)

            # 5. Guardar transacción
            trans = {
                "id_transaccion": nuevo_id,
                "fecha": fecha,
                "descripcion": descripcion,
                "valor": valor_ajustado,
                "tipo": tipo,
                "cuenta": cuenta,
                "categoria": categoria,
                "saldo_en_cuenta": saldo_en_cuenta,
                "saldo_inicial": saldo_inicial_global,
                "saldo_final": saldo_final_global,
            }
            db.collection("transacciones").add(trans)

            # 6. Actualizar saldos_diarios de ESA fecha
            doc_ref = db.collection("saldos_diarios").document(fecha)
            doc = doc_ref.get()
            datos = doc.to_dict() if doc.exists else {}
            datos["fecha"] = fecha
            datos.setdefault("saldo_inicial", saldo_inicial_global)
            datos["saldo_final"] = saldo_final_global
            doc_ref.set(datos, merge=True)

            # Volvemos a la misma fecha de trabajo
            return redirect(url_for("transacciones", fecha=fecha_trabajo))

       # ------------------- GET: mostrar SOLO la fecha de trabajo -------------------

    # Cuentas para el combo
    cuentas_docs = db.collection("cuentas").stream()
    cuentas_lista = [d.to_dict() for d in cuentas_docs]

    # Transacciones SOLO de esa fecha de trabajo
    trans_docs = (
        db.collection("transacciones")
        .where("fecha", "==", fecha_trabajo)
        .order_by("id_transaccion")
        .stream()
    )

    trans_lista = []

    for d in trans_docs:
        data = d.to_dict()

        # valor_mostrado: positivo para ingresos, negativo para gastos
        valor = float(data.get("valor", 0))
        tipo_t = (data.get("tipo", "") or "").lower()
        if tipo_t == "gasto":
            data["valor_mostrado"] = -abs(valor)
        else:
            data["valor_mostrado"] = abs(valor)

        # saldo_global_mostrado: usar SIEMPRE el saldo_final guardado en la transacción
        data["saldo_global_mostrado"] = data.get("saldo_final", 0)

        trans_lista.append(data)

    return render_template(
        "transacciones.html",
        cuentas=cuentas_lista,
        transacciones=trans_lista,
        formatear_cop=formatear_cop,
        error=error,
        categorias_gasto=CATEGORIAS_GASTO,
        categorias_ingreso=CATEGORIAS_INGRESO,
        fecha_trabajo=fecha_trabajo,
    )



# ----------- VISTAS HISTÓRICAS: INGRESOS / GASTOS -----------


@app.route("/ingresos")
@login_requerido
def ingresos_historicos():
    docs = (
        db.collection("transacciones")
        .where("tipo", "==", "ingreso")
        .order_by("fecha")
        .order_by("id_transaccion")
        .stream()
    )

    ingresos = []
    for d in docs:
        data = d.to_dict()
        # Siempre valor positivo para mostrar
        valor = abs(float(data.get("valor", 0)))
        data["valor_mostrado"] = valor
        ingresos.append(data)

    return render_template(
        "ingresos.html",
        ingresos=ingresos,
        formatear_cop=formatear_cop,
    )


@app.route("/gastos")
@login_requerido
def gastos_historicos():
    docs = (
        db.collection("transacciones")
        .where("tipo", "==", "gasto")
        .order_by("fecha")
        .order_by("id_transaccion")
        .stream()
    )

    gastos = []
    for d in docs:
        data = d.to_dict()
        valor = abs(float(data.get("valor", 0)))
        data["valor_mostrado"] = valor
        gastos.append(data)

    return render_template(
        "gastos.html",
        gastos=gastos,
        formatear_cop=formatear_cop,
    )


# ----------- RESUMEN DIARIO Y MENSUAL -----------


def calcular_resumen_diario():
    """
    Devuelve una lista de días con:
      fecha, ingresos, gastos, diferencia, saldo_inicial, saldo_final
    más los totales.

    Se basa en los campos saldo_inicial y saldo_final guardados en cada
    transacción, para que no dependa del valor actual de las cuentas.
    """
    docs = (
        db.collection("transacciones")
        .order_by("fecha")
        .order_by("id_transaccion")
        .stream()
    )

    resumen = []
    fecha_actual = None
    saldo_corriente = None

    for d in docs:
        data = d.to_dict()
        fecha = data.get("fecha")
        if not fecha:
            continue

        valor = float(data.get("valor", 0))
        tipo_t = (data.get("tipo", "") or "").lower()

        # Para la primera transacción usamos su saldo_inicial almacenado
        if saldo_corriente is None:
            saldo_corriente = float(data.get("saldo_inicial", 0))

        if fecha != fecha_actual:
            # cerramos día anterior
            if fecha_actual is not None:
                resumen[-1]["saldo_final"] = saldo_corriente

            fecha_actual = fecha
            resumen.append(
                {
                    "fecha": fecha,
                    "ingresos": 0.0,
                    "gastos": 0.0,
                    "diferencia": 0.0,
                    "saldo_inicial": saldo_corriente,
                    "saldo_final": None,
                }
            )

        if tipo_t == "gasto":
            resumen[-1]["gastos"] += abs(valor)
            saldo_corriente += -abs(valor)
        else:
            resumen[-1]["ingresos"] += abs(valor)
            saldo_corriente += abs(valor)

    # Cerrar último día
    if resumen:
        resumen[-1]["saldo_final"] = saldo_corriente
        for r in resumen:
            r["diferencia"] = r["ingresos"] - r["gastos"]

    totales = {
        "ingresos": sum(r["ingresos"] for r in resumen),
        "gastos": sum(r["gastos"] for r in resumen),
        "diferencia": sum(r["diferencia"] for r in resumen),
    }

    return resumen, totales

def calcular_rango_fechas(periodo, fecha_desde_str=None, fecha_hasta_str=None):
    """
    Devuelve (desde_iso, hasta_iso) según el tipo de periodo:
    - 'personalizado': usa las fechas que vengan del formulario
    - 'ultimos_7_dias'
    - 'ultimos_30_dias'
    - 'este_mes'
    - 'mes_anterior'
    - 'este_anio'
    - 'anio_anterior'
    """
    hoy = date.today()

    if periodo == "personalizado":
        if fecha_desde_str and fecha_hasta_str:
            return fecha_desde_str, fecha_hasta_str
        # si no mandan nada, por defecto últimos 30 días
        desde = hoy - timedelta(days=30)
        return desde.isoformat(), hoy.isoformat()

    if periodo == "ultimos_7_dias":
        desde = hoy - timedelta(days=7)
        return desde.isoformat(), hoy.isoformat()

    if periodo == "ultimos_30_dias":
        desde = hoy - timedelta(days=30)
        return desde.isoformat(), hoy.isoformat()

    if periodo == "este_mes":
        desde = hoy.replace(day=1)
        # hasta = hoy
        return desde.isoformat(), hoy.isoformat()

    if periodo == "mes_anterior":
        primer_dia_mes_actual = hoy.replace(day=1)
        ultimo_dia_mes_anterior = primer_dia_mes_actual - timedelta(days=1)
        desde = ultimo_dia_mes_anterior.replace(day=1)
        hasta = ultimo_dia_mes_anterior
        return desde.isoformat(), hasta.isoformat()

    if periodo == "este_anio":
        desde = hoy.replace(month=1, day=1)
        return desde.isoformat(), hoy.isoformat()

    if periodo == "anio_anterior":
        desde = hoy.replace(year=hoy.year - 1, month=1, day=1)
        hasta = hoy.replace(year=hoy.year - 1, month=12, day=31)
        return desde.isoformat(), hasta.isoformat()

    # Por defecto: últimos 30 días
    desde = hoy - timedelta(days=30)
    return desde.isoformat(), hoy.isoformat()


def filtrar_y_resumir(transacciones, tipo, desde_iso, hasta_iso, categorias_sel):
    """
    Filtra la lista de transacciones (lista de dicts) por:
    - tipo: 'ingreso', 'gasto' o 'todos'
    - rango de fechas [desde_iso, hasta_iso]
    - categorías seleccionadas (lista)

    Devuelve (lista_filtrada, resumen_dict)
    """
    desde_iso = desde_iso or "0000-01-01"
    hasta_iso = hasta_iso or "9999-12-31"

    categorias_sel = categorias_sel or []

    filtradas = []
    total_ingresos = 0.0
    total_gastos = 0.0

    for data in transacciones:
        fecha = data.get("fecha")
        if not fecha:
            continue

        tipo_t = (data.get("tipo", "") or "").lower()
        if tipo != "todos" and tipo_t != tipo:
            continue

        # fechas en formato YYYY-MM-DD → comparación de strings sirve
        if fecha < desde_iso or fecha > hasta_iso:
            continue

        categoria = data.get("categoria", "")
        if categorias_sel and categoria not in categorias_sel:
            continue

        # calcular valor_mostrado
        valor = float(data.get("valor", 0))
        if tipo_t == "gasto":
            data["valor_mostrado"] = -abs(valor)
            total_gastos += abs(valor)
        else:
            data["valor_mostrado"] = abs(valor)
            if valor > 0:
                total_ingresos += valor

        filtradas.append(data)

    diferencia = total_ingresos - total_gastos

    resumen = {
        "total_ingresos": total_ingresos,
        "total_gastos": total_gastos,
        "diferencia": diferencia,
        "cantidad": len(filtradas),
        "desde": desde_iso,
        "hasta": hasta_iso,
    }

    return filtradas, resumen

@app.route("/resumen-diario")
@login_requerido
def resumen_diario():
    resumen, totales = calcular_resumen_diario()
    return render_template(
        "resumen_diario.html",
        filas=resumen,
        totales=totales,
        formatear_cop=formatear_cop,
    )


@app.route("/resumen-mensual")
@login_requerido
def resumen_mensual():
    resumen_diario, _ = calcular_resumen_diario()

    # Agrupar por mes YYYY-MM
    resumen_mes = {}
    for r in resumen_diario:
        clave_mes = r["fecha"][:7]  # 'YYYY-MM'
        if clave_mes not in resumen_mes:
            resumen_mes[clave_mes] = {
                "mes": clave_mes,
                "ingresos": 0.0,
                "gastos": 0.0,
                "diferencia": 0.0,
                "saldo_inicial": r["saldo_inicial"],
                "saldo_final": r["saldo_final"],
            }
        resumen_mes[clave_mes]["ingresos"] += r["ingresos"]
        resumen_mes[clave_mes]["gastos"] += r["gastos"]
        resumen_mes[clave_mes]["saldo_final"] = r["saldo_final"]

    filas = []
    for mes, datos in sorted(resumen_mes.items()):
        datos["diferencia"] = datos["ingresos"] - datos["gastos"]
        filas.append(datos)

    totales = {
        "ingresos": sum(f["ingresos"] for f in filas),
        "gastos": sum(f["gastos"] for f in filas),
        "diferencia": sum(f["diferencia"] for f in filas),
    }

    return render_template(
        "resumen_mensual.html",
        filas=filas,
        totales=totales,
        formatear_cop=formatear_cop,
    )

@app.route("/analisis", methods=["GET"])
@login_requerido
def analisis():
    # -------- Parámetros del formulario (GET) --------
    tipo = request.args.get("tipo", "todos")  # 'ingreso', 'gasto', 'todos'

    periodo = request.args.get("periodo", "este_mes")
    fecha_desde = request.args.get("fecha_desde")  # solo usado si periodo = personalizado
    fecha_hasta = request.args.get("fecha_hasta")

    periodo_comp = request.args.get("periodo_comp", "")
    fecha_desde_comp = request.args.get("fecha_desde_comp")
    fecha_hasta_comp = request.args.get("fecha_hasta_comp")

    categorias_sel = request.args.getlist("categorias")

    # -------- Cargar TODAS las transacciones una sola vez --------
    docs = (
        db.collection("transacciones")
        .order_by("fecha")
        .order_by("id_transaccion")
        .stream()
    )
    todas = [d.to_dict() for d in docs]

    # -------- Periodo principal --------
    desde_iso, hasta_iso = calcular_rango_fechas(periodo, fecha_desde, fecha_hasta)
    trans_principal, resumen_principal = filtrar_y_resumir(
        todas, tipo, desde_iso, hasta_iso, categorias_sel
    )

    # -------- Periodo de comparación (opcional) --------
    trans_comp = []
    resumen_comp = None

    if periodo_comp or (fecha_desde_comp and fecha_hasta_comp):
        desde_comp_iso, hasta_comp_iso = calcular_rango_fechas(
            periodo_comp or "personalizado",
            fecha_desde_comp,
            fecha_hasta_comp,
        )
        trans_comp, resumen_comp = filtrar_y_resumir(
            todas, tipo, desde_comp_iso, hasta_comp_iso, categorias_sel
        )

    # Lista de categorías para el multiselect
    categorias_todas = CATEGORIAS_INGRESO + CATEGORIAS_GASTO

    return render_template(
        "analisis.html",
        tipo=tipo,
        periodo=periodo,
        fecha_desde=desde_iso,
        fecha_hasta=hasta_iso,
        periodo_comp=periodo_comp,
        fecha_desde_comp=fecha_desde_comp or "",
        fecha_hasta_comp=fecha_hasta_comp or "",
        categorias_todas=categorias_todas,
        categorias_seleccionadas=categorias_sel,
        trans_principal=trans_principal,
        resumen_principal=resumen_principal,
        trans_comp=trans_comp,
        resumen_comp=resumen_comp,
        formatear_cop=formatear_cop,
    )

@app.route("/reporte-general", methods=["GET"])
@login_requerido
def reporte_general():
    # 1) Parámetros del filtro
    periodo = request.args.get("periodo", "este_mes")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")

    # Usamos el helper que ya tenemos
    desde_iso, hasta_iso = calcular_rango_fechas(periodo, fecha_desde, fecha_hasta)

    # 2) Cargar todas las transacciones y filtrar por rango
    docs = (
        db.collection("transacciones")
        .order_by("fecha")
        .order_by("id_transaccion")
        .stream()
    )

    ingresos_por_cat = {}
    gastos_por_cat = {}
    total_ingresos = 0.0
    total_gastos = 0.0

    for d in docs:
        data = d.to_dict()
        fecha = data.get("fecha")
        if not fecha:
            continue

        if fecha < desde_iso or fecha > hasta_iso:
            continue

        tipo_t = (data.get("tipo", "") or "").lower()
        categoria = data.get("categoria", "Sin categoría")
        valor = float(data.get("valor", 0))

        if tipo_t == "gasto":
            valor_abs = abs(valor)
            gastos_por_cat[categoria] = gastos_por_cat.get(categoria, 0) + valor_abs
            total_gastos += valor_abs
        else:
            # ingreso (valor guardado ya es positivo)
            valor_abs = abs(valor)
            ingresos_por_cat[categoria] = ingresos_por_cat.get(categoria, 0) + valor_abs
            total_ingresos += valor_abs

    # 3) Agrupar ingresos como en tu Excel
    sueldo_deiner = ingresos_por_cat.get("Sueldo Deiner", 0)
    sueldo_sole = ingresos_por_cat.get("Sueldo Sole", 0)
    total_sueldos = sueldo_deiner + sueldo_sole

    negocios = ingresos_por_cat.get("Negocios", 0)
    ariadna_babyshop = ingresos_por_cat.get("Ariadna Babyshop", 0)
    total_negocios = negocios + ariadna_babyshop

    otros_ingresos = ingresos_por_cat.get("Otros ingresos", 0)
    prestamos = ingresos_por_cat.get("Prestamos", 0)
    total_otros = otros_ingresos + prestamos

    saldo_periodo = total_ingresos - total_gastos

    # 4) Para mostrar todas las categorías de gastos en orden
    #    (usamos la lista CATEGORIAS_GASTO ya definida arriba)
    gastos_ordenados = []
    for cat in CATEGORIAS_GASTO:
        gastos_ordenados.append({
            "categoria": cat,
            "valor": gastos_por_cat.get(cat, 0)
        })

    # 5) Texto del rango
    rango_texto = f"{desde_iso} a {hasta_iso}"

    return render_template(
        "reporte_general.html",
        periodo=periodo,
        fecha_desde=desde_iso,
        fecha_hasta=hasta_iso,
        rango_texto=rango_texto,
        total_ingresos=total_ingresos,
        total_gastos=total_gastos,
        saldo_periodo=saldo_periodo,
        total_sueldos=total_sueldos,
        sueldo_deiner=sueldo_deiner,
        sueldo_sole=sueldo_sole,
        total_negocios=total_negocios,
        negocios=negocios,
        ariadna_babyshop=ariadna_babyshop,
        total_otros=total_otros,
        otros_ingresos=otros_ingresos,
        prestamos=prestamos,
        gastos_ordenados=gastos_ordenados,
        formatear_cop=formatear_cop,
    )

@app.context_processor
def inject_helpers():
    return dict(formatear_cop=formatear_cop)


@app.route("/test_db")
def test_db():
    doc_ref = db.collection("pruebas").document()
    doc_ref.set({"mensaje": "Hola desde Flask", "autor": "Deiner", "ok": True})

    docs = db.collection("pruebas").stream()
    mensajes = [d.to_dict() for d in docs]

    return {"total_documentos": len(mensajes), "datos": mensajes}


if __name__ == "__main__":
    app.run(debug=True)
