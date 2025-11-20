from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Inicio OK"

@app.route("/test_db")
def test_db():
    return {"msg": "Ruta test_db funcionando"}

if __name__ == "__main__":
    app.run(debug=True)
