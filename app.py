from flask import Flask, render_template, request, redirect, url_for
from babel.numbers import format_currency

from datetime import date, timedelta
import os
import json

import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

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
    return format_currency(valor, "COP", locale="es_CO")


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


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/cuentas", methods=["GET", "POST"])
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
        tipo = request.form.get("tipo", "ingreso")
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

            # 2.b Actualizar el saldo de la cuenta en la colección "cuentas"
            cuentas_query = (
                db.collection("cuentas")
                .where("nombre", "==", cuenta)
                .limit(1)
                .stream()
            )

            for c_doc in cuentas_query:
                # Aquí usamos saldo_en_cuenta como saldo actual de la cuenta
                c_doc.reference.update({"saldo_inicial": saldo_en_cuenta})

            # 3. Saldo global (saldo_inicial / saldo_final)
            saldo_inicial_global = None
            ult_docs = (
                db.collection("transacciones")
                .order_by("id_transaccion")
                .stream()
            )
            for t in ult_docs:
                data_t = t.to_dict()
                saldo_inicial_global = data_t.get("saldo_final")

            if saldo_inicial_global is None:
                cuentas_docs = db.collection("cuentas").stream()
                total_cuentas = sum(
                    c.to_dict().get("saldo_inicial", 0) for c in cuentas_docs
                )
                saldo_inicial_global = total_cuentas

            saldo_final_global = saldo_inicial_global + valor_ajustado

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

    # Saldo base = suma de saldos iniciales de todas las cuentas
    saldo_base = sum(c.get("saldo_inicial", 0) for c in cuentas_lista)

    # Transacciones SOLO de esa fecha de trabajo
    trans_docs = (
        db.collection("transacciones")
        .where("fecha", "==", fecha_trabajo)
        .order_by("id_transaccion")
        .stream()
    )

    trans_lista = []
    saldo_global = saldo_base

    for d in trans_docs:
        data = d.to_dict()

        valor = float(data.get("valor", 0))
        tipo_t = (data.get("tipo", "") or "").lower()
        if tipo_t == "gasto":
            valor_signed = -abs(valor)
        else:
            valor_signed = abs(valor)

        data["valor_mostrado"] = valor_signed
        data["saldo_inicial_global"] = saldo_global
        saldo_global = saldo_global + valor_signed
        data["saldo_final_global"] = saldo_global

        trans_lista.append(data)

    return render_template(
        "transacciones.html",
        cuentas=cuentas_lista,
        transacciones=trans_lista,
        formatear_cop=formatear_cop,
        error=error,
        categorias_gasto=CATEGORIAS_GASTO,
        categorias_ingreso=CATEGORIAS_INGRESO,
        fecha_trabajo=fecha_trabajo,  # se la mandamos al HTML
    )


# ----------- VISTAS HISTÓRICAS: INGRESOS / GASTOS -----------


@app.route("/ingresos")
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


@app.route("/resumen-diario")
def resumen_diario():
    resumen, totales = calcular_resumen_diario()
    return render_template(
        "resumen_diario.html",
        filas=resumen,
        totales=totales,
        formatear_cop=formatear_cop,
    )


@app.route("/resumen-mensual")
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


@app.route("/test_db")
def test_db():
    doc_ref = db.collection("pruebas").document()
    doc_ref.set({"mensaje": "Hola desde Flask", "autor": "Deiner", "ok": True})

    docs = db.collection("pruebas").stream()
    mensajes = [d.to_dict() for d in docs]

    return {"total_documentos": len(mensajes), "datos": mensajes}


if __name__ == "__main__":
    app.run(debug=True)
