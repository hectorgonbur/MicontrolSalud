import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, time, timedelta
import time as pytime

# ---------------------------
# Database setup and helpers
# ---------------------------
DB_PATH = "medication_tracker.db"

def get_db_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # so we can access columns by name
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Medications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            total_stock INTEGER NOT NULL,      -- initial total pills
            current_stock INTEGER NOT NULL,    -- remaining pills
            frequency_hours INTEGER NOT NULL,
            dose INTEGER NOT NULL,              -- pills per intake
            food_requirement TEXT               -- e.g., "con comida", "sin comida"
        )
    """)

    # Intakes table (scheduled and taken events)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS intakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL,
            scheduled_time TIMESTAMP NOT NULL,
            taken_time TIMESTAMP,
            taken BOOLEAN DEFAULT 0,
            FOREIGN KEY(medication_id) REFERENCES medications(id)
        )
    """)

    # Meals table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_time TIMESTAMP NOT NULL,
            description TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def add_medication(name, total_stock, frequency_hours, dose, food_requirement):
    """Insert a new medication into the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO medications (name, total_stock, current_stock, frequency_hours, dose, food_requirement)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, total_stock, total_stock, frequency_hours, dose, food_requirement))
    conn.commit()
    conn.close()

def get_all_medications():
    """Return a list of all medications as dictionaries."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM medications ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def ensure_today_intakes(med_id, med_frequency, med_dose):
    """
    For a given medication, generate today's scheduled intakes if they don't already exist.
    Scheduled times are placed at intervals starting from midnight: 00:00, frequency, 2*frequency, etc.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if any intake for this medication already exists for today
    today_start = datetime.combine(date.today(), time.min)
    today_end = datetime.combine(date.today(), time.max)
    cursor.execute("""
        SELECT id FROM intakes
        WHERE medication_id = ? AND scheduled_time BETWEEN ? AND ?
        LIMIT 1
    """, (med_id, today_start, today_end))
    if cursor.fetchone():
        conn.close()
        return   # already have intakes for today

    # Generate scheduled times
    for hour in range(0, 24, med_frequency):
        scheduled = datetime.combine(date.today(), time(hour, 0))
        # Ensure we don't go beyond today (just in case frequency doesn't divide 24 evenly)
        if scheduled <= today_end:
            cursor.execute("""
                INSERT INTO intakes (medication_id, scheduled_time, taken_time, taken)
                VALUES (?, ?, ?, ?)
            """, (med_id, scheduled, None, 0))
    conn.commit()
    conn.close()

def mark_intake_taken(intake_id, medication_id):
    """
    Mark a specific intake as taken:
    - Set taken_time to now, taken=1.
    - Decrease current_stock of the medication by the dose.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get the dose for this medication
    cursor.execute("SELECT dose FROM medications WHERE id = ?", (medication_id,))
    dose = cursor.fetchone()["dose"]

    # Update intake
    cursor.execute("""
        UPDATE intakes SET taken_time = ?, taken = 1
        WHERE id = ?
    """, (datetime.now(), intake_id))

    # Decrease stock
    cursor.execute("""
        UPDATE medications SET current_stock = current_stock - ?
        WHERE id = ?
    """, (dose, medication_id))

    conn.commit()
    conn.close()

def add_meal(description, meal_time=None):
    """Log a meal with optional time (default now)."""
    if meal_time is None:
        meal_time = datetime.now()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO meals (meal_time, description) VALUES (?, ?)
    """, (meal_time, description))
    conn.commit()
    conn.close()

def get_today_intakes():
    """
    Return all scheduled intakes for today, enriched with medication details.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    today_start = datetime.combine(date.today(), time.min)
    today_end = datetime.combine(date.today(), time.max)
    cursor.execute("""
        SELECT i.id, i.medication_id, i.scheduled_time, i.taken_time, i.taken,
               m.name, m.dose, m.food_requirement, m.current_stock
        FROM intakes i
        JOIN medications m ON i.medication_id = m.id
        WHERE i.scheduled_time BETWEEN ? AND ?
        ORDER BY i.scheduled_time
    """, (today_start, today_end))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_today_meals():
    """Return all meals logged today."""
    conn = get_db_connection()
    cursor = conn.cursor()
    today_start = datetime.combine(date.today(), time.min)
    today_end = datetime.combine(date.today(), time.max)
    cursor.execute("""
        SELECT * FROM meals
        WHERE meal_time BETWEEN ? AND ?
        ORDER BY meal_time
    """, (today_start, today_end))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_stock_alerts():
    """
    Return list of medications with low stock (< 5 remaining doses).
    A dose is defined as the medication's dose field (pills per intake).
    Remaining doses = floor(current_stock / dose).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, current_stock, dose
        FROM medications
    """)
    rows = cursor.fetchall()
    conn.close()
    alerts = []
    for row in rows:
        remaining_doses = row["current_stock"] // row["dose"]
        if remaining_doses < 5:
            alerts.append({
                "name": row["name"],
                "remaining_doses": remaining_doses,
                "current_stock": row["current_stock"]
            })
    return alerts

def get_history_data(days=7):
    """
    Return daily compliance (fraction of taken intakes) for the last `days` days.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    end_date = date.today()
    start_date = end_date - timedelta(days=days-1)

    # For each day, count scheduled and taken intakes
    history = []
    for single_date in (start_date + timedelta(n) for n in range(days)):
        day_start = datetime.combine(single_date, time.min)
        day_end = datetime.combine(single_date, time.max)
        cursor.execute("""
            SELECT COUNT(*) as scheduled,
                   SUM(CASE WHEN taken = 1 THEN 1 ELSE 0 END) as taken
            FROM intakes
            WHERE scheduled_time BETWEEN ? AND ?
        """, (day_start, day_end))
        row = cursor.fetchone()
        scheduled = row["scheduled"] or 0
        taken = row["taken"] or 0
        compliance = taken / scheduled if scheduled > 0 else 0
        history.append({
            "date": single_date,
            "scheduled": scheduled,
            "taken": taken,
            "compliance": compliance
        })
    conn.close()
    return history

# ---------------------------
# Streamlit UI
# ---------------------------
def main():
    st.set_page_config(page_title="Seguimiento de Tratamiento", layout="wide")
    st.title("💊 Seguimiento de Tratamiento y Medicación")

    # Initialize database
    init_db()

    # Sidebar: Add new medication
    with st.sidebar:
        st.header("⚙️ Configuración del Tratamiento")
        with st.form("new_med_form", clear_on_submit=True):
            med_name = st.text_input("Nombre del medicamento")
            total_stock = st.number_input("Stock total (pastillas)", min_value=1, step=1)
            frequency = st.number_input("Frecuencia (cada X horas)", min_value=1, max_value=24, step=1)
            dose = st.number_input("Dosis (pastillas por toma)", min_value=1, step=1)
            food_req = st.selectbox("¿Con/sin comida?", ["sin comida", "con comida", "indiferente"])
            submitted = st.form_submit_button("Agregar medicamento")
            if submitted and med_name:
                add_medication(med_name, total_stock, frequency, dose, food_req)
                st.success("Medicamento agregado")
                pytime.sleep(1)
                st.rerun()

    # Ensure today's intakes exist for all medications
    meds = get_all_medications()
    for med in meds:
        ensure_today_intakes(med["id"], med["frequency_hours"], med["dose"])

    # Main area: Dashboard
    col1, col2 = st.columns([2, 1])

    with col1:
        st.header("📋 Tareas de Hoy")
        today_intakes = get_today_intakes()
        if today_intakes:
            for intake in today_intakes:
                with st.container(border=True):
                    cols = st.columns([2, 1, 1, 1])
                    scheduled_time = datetime.fromisoformat(intake["scheduled_time"]).strftime("%H:%M")
                    cols[0].markdown(f"**{intake['name']}**  \n{scheduled_time} - {intake['dose']} pastilla(s)  \n*{intake['food_requirement']}*")
                    if intake["taken"]:
                        taken_time = datetime.fromisoformat(intake["taken_time"]).strftime("%H:%M")
                        cols[1].success(f"✅ Tomado a las {taken_time}")
                    else:
                        # Check if enough stock
                        remaining_doses = intake["current_stock"] // intake["dose"]
                        if remaining_doses <= 0:
                            cols[1].warning("Sin stock")
                        else:
                            # Button to mark as taken
                            if cols[1].button("✔️ Marcar tomado", key=f"take_{intake['id']}"):
                                mark_intake_taken(intake["id"], intake["medication_id"])
                                st.rerun()
        else:
            st.info("No hay medicamentos configurados. Agrega uno en la barra lateral.")

        st.header("🍽️ Registro de Comidas")
        with st.form("meal_form", clear_on_submit=True):
            meal_desc = st.text_input("¿Qué comiste?")
            meal_time = st.time_input("Hora", value=datetime.now().time())
            submit_meal = st.form_submit_button("Registrar comida")
            if submit_meal and meal_desc:
                meal_datetime = datetime.combine(date.today(), meal_time)
                add_meal(meal_desc, meal_datetime)
                st.success("Comida registrada")
                st.rerun()

        # Show today's meals
        meals = get_today_meals()
        if meals:
            for meal in meals:
                meal_time = datetime.fromisoformat(meal["meal_time"]).strftime("%H:%M")
                st.caption(f"{meal_time} - {meal['description']}")

    with col2:
        st.header("📦 Inventario")
        meds = get_all_medications()
        if meds:
            df_meds = pd.DataFrame(meds)
            # Show current stock and remaining doses
            df_meds["dosis_restantes"] = df_meds["current_stock"] // df_meds["dose"]
            for _, row in df_meds.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{row['name']}**")
                    st.markdown(f"Stock: {row['current_stock']} pastillas")
                    remaining = row['dosis_restantes']
                    if remaining < 5:
                        st.error(f"⚠️ ¡Solo {remaining} dosis restantes!")
                    else:
                        st.info(f"Dosis restantes: {remaining}")
        else:
            st.info("Sin medicamentos.")

        # Stock alerts (red/yellow)
        alerts = get_stock_alerts()
        if alerts:
            st.subheader("🚨 Alertas de Stock Bajo")
            for alert in alerts:
                st.warning(f"{alert['name']}: {alert['remaining_doses']} dosis ({alert['current_stock']} pastillas)")

        st.header("📊 Historial (últimos 7 días)")
        history = get_history_data(7)
        if history:
            df_hist = pd.DataFrame(history)
            # Simple bar chart of compliance
            df_hist["fecha"] = pd.to_datetime(df_hist["date"]).dt.strftime("%d/%m")
            st.bar_chart(df_hist.set_index("fecha")["compliance"])
            # Optionally show table
            with st.expander("Ver tabla detallada"):
                st.dataframe(df_hist[["fecha", "scheduled", "taken", "compliance"]].rename(
                    columns={"fecha": "Fecha", "scheduled": "Programadas", "taken": "Tomadas", "compliance": "Cumplimiento"}
                ))
        else:
            st.caption("No hay datos aún.")

if __name__ == "__main__":
    main()
