import time
import random
import asyncio
import sqlite3
import re
import json
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- 1. GESTION BASE DE DONNEES ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Table ACTIONS (Epée, Boule de feu, Soin...)
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT,
        formule_degats TEXT, -- Ex: "2d6+4"
        type_action TEXT     -- "attaque" ou "soin"
    )''')

    # Table COMBATTANTS (PJ et Monstres)
    # On stocke la liste des ID d'actions sous forme de texte "[1, 3]"
    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT,
        type_entite TEXT, -- "PJ" ou "MONSTRE"
        hp INTEGER,
        ac INTEGER,
        init_bonus INTEGER,
        hit_bonus INTEGER,
        nb_attaques INTEGER,
        actions_ids TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# --- 2. OUTILS DE DES (DICE PARSER) ---
def lancer_des(formule: str):
    """Transforme '2d6+4' en un résultat chiffré."""
    if not formule: return 0
    
    # Nettoyage
    formule = formule.lower().replace(" ", "")
    
    total = 0
    # Regex pour trouver XdY
    parties = re.split(r'([+-])', formule)
    
    current_sign = 1
    for p in parties:
        if p == '+': current_sign = 1
        elif p == '-': current_sign = -1
        elif 'd' in p:
            try:
                nb, faces = map(int, p.split('d'))
                jet = sum(random.randint(1, faces) for _ in range(nb))
                total += jet * current_sign
            except: pass
        else:
            try:
                total += int(p) * current_sign
            except: pass
            
    return max(0, total)

# --- 3. MODELES API ---
class ActionModel(BaseModel):
    nom: str
    formule: str
    type_action: str = "attaque"

class FighterModel(BaseModel):
    nom: str
    type_entite: str
    hp: int
    ac: int
    init_bonus: int
    hit_bonus: int
    nb_attaques: int
    actions_ids: List[int] # Liste des ID d'actions choisies

class SimuRequest(BaseModel):
    iterations: int
    pj_ids: List[int]
    monstre_ids: List[int]

# --- 4. MOTEUR DE COMBAT D'EQUIPE ---
class EntiteCombat:
    def __init__(self, data, actions_db):
        self.id = data['id']
        self.nom = data['nom']
        self.team = data['type_entite'] # PJ ou MONSTRE
        self.hp = data['hp']
        self.hp_max = data['hp']
        self.ac = data['ac']
        self.init = random.randint(1, 20) + data['init_bonus']
        self.hit_bonus = data['hit_bonus']
        self.nb_attaques = data['nb_attaques']
        
        # Récupération des vrais objets actions
        ids = json.loads(data['actions_ids']) if data['actions_ids'] else []
        self.actions = [act for act in actions_db if act['id'] in ids]

    def est_vivant(self):
        return self.hp > 0

    def choisir_action(self):
        if not self.actions: return None
        return random.choice(self.actions) # IA Simpliste : au hasard

def simuler_bataille(args):
    # args contient (team_pj_data, team_m_data, actions_list)
    team_pj_raw, team_m_raw, actions_list = args
    
    # Instanciation
    team_pj = [EntiteCombat(d, actions_list) for d in team_pj_raw]
    team_m = [EntiteCombat(d, actions_list) for d in team_m_raw]
    
    tous = team_pj + team_m
    rounds = 0
    
    while any(p.est_vivant() for p in team_pj) and any(m.est_vivant() for m in team_m):
        rounds += 1
        if rounds > 100: break
        
        # Tri par initiative
        tous.sort(key=lambda x: x.init, reverse=True)
        
        for acteur in tous:
            if not acteur.est_vivant(): continue
            
            # Identifier les ennemis vivants
            ennemis = [e for e in (team_m if acteur.team == 'PJ' else team_pj) if e.est_vivant()]
            if not ennemis: break # Victoire
            
            # Action Economy : Multi-attaques
            for _ in range(acteur.nb_attaques):
                cible = random.choice(ennemis) # Cible au hasard
                if not cible.est_vivant(): 
                    # Si la cible meurt entre deux attaques, changer de cible
                    ennemis = [e for e in (team_m if acteur.team == 'PJ' else team_pj) if e.est_vivant()]
                    if not ennemis: break
                    cible = random.choice(ennemis)

                action = acteur.choisir_action()
                if not action: 
                    # Attaque par défaut (coup de poing)
                    if random.randint(1, 20) + acteur.hit_bonus >= cible.ac:
                        cible.hp -= 1
                    continue

                if action['type_action'] == 'soin':
                    # Se soigne soi-même (simplifié)
                    heal = lancer_des(action['formule_degats'])
                    acteur.hp = min(acteur.hp + heal, acteur.hp_max)
                else:
                    # Attaque
                    jet = random.randint(1, 20)
                    if jet == 20: # Critique
                        dmg = lancer_des(action['formule_degats']) * 2 # Brutal mais simple
                        cible.hp -= dmg
                    elif jet + acteur.hit_bonus >= cible.ac:
                        dmg = lancer_des(action['formule_degats'])
                        cible.hp -= dmg

    victoire_pj = any(p.est_vivant() for p in team_pj)
    pertes_pj = sum(1 for p in team_pj if not p.est_vivant())
    
    return {"victoire_pj": victoire_pj, "rounds": rounds, "pertes_pj": pertes_pj}

def process_parallel(payload):
    # 1. Récupérer les données depuis la DB pour les passer aux process
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Charger Actions
    c.execute("SELECT * FROM actions")
    all_actions = [dict(r) for r in c.fetchall()]
    
    # Charger PJs sélectionnés
    pjs_data = []
    if payload.pj_ids:
        placeholders = ','.join('?' for _ in payload.pj_ids)
        c.execute(f"SELECT * FROM combattants WHERE id IN ({placeholders})", payload.pj_ids)
        pjs_data = [dict(r) for r in c.fetchall()]

    # Charger Monstres sélectionnés
    m_data = []
    if payload.monstre_ids:
        placeholders = ','.join('?' for _ in payload.monstre_ids)
        c.execute(f"SELECT * FROM combattants WHERE id IN ({placeholders})", payload.monstre_ids)
        m_data = [dict(r) for r in c.fetchall()]
    
    conn.close()

    if not pjs_data or not m_data:
        return {"error": "Il faut au moins 1 PJ et 1 Monstre"}

    # 2. Lancer la simulation
    with ProcessPoolExecutor() as executor:
        # On prépare les arguments pour chaque simulation
        # Note: On passe les dictionnaires bruts pour éviter les problèmes de pickling d'objets complexes
        args = (pjs_data, m_data, all_actions)
        results = list(executor.map(simuler_bataille, [args] * payload.iterations))

    victoires = sum(1 for r in results if r['victoire_pj'])
    total_rounds = sum(r['rounds'] for r in results)
    avg_pertes = sum(r['pertes_pj'] for r in results) / payload.iterations

    return {
        "win_rate": (victoires / payload.iterations) * 100,
        "avg_rounds": total_rounds / payload.iterations,
        "avg_deaths": avg_pertes,
        "simulations": payload.iterations
    }

# --- 5. ROUTES API ---

@app.post("/api/action/add")
def add_action(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO actions (nom, formule_degats, type_action) VALUES (?, ?, ?)", 
              (a.nom, a.formule, a.type_action))
    conn.commit()
    conn.close()
    return "ok"

@app.get("/api/action/list")
def list_actions():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    res = [dict(r) for r in conn.cursor().execute("SELECT * FROM actions").fetchall()]
    conn.close()
    return res

@app.post("/api/fighter/add")
def add_fighter(f: FighterModel):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    ids_json = json.dumps(f.actions_ids)
    c.execute("""INSERT INTO combattants 
        (nom, type_entite, hp, ac, init_bonus, hit_bonus, nb_attaques, actions_ids) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (f.nom, f.type_entite, f.hp, f.ac, f.init_bonus, f.hit_bonus, f.nb_attaques, ids_json))
    conn.commit()
    conn.close()
    return "ok"

@app.get("/api/fighter/list")
def list_fighters(type: str = None):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM combattants"
    args = []
    if type:
        query += " WHERE type_entite = ?"
        args = [type]
    res = [dict(r) for r in conn.cursor().execute(query, args).fetchall()]
    conn.close()
    return res

@app.post("/api/simulate")
async def run_simu(req: SimuRequest):
    loop = asyncio.get_running_loop()
    start = time.time()
    stats = await loop.run_in_executor(None, process_parallel, req)
    stats['time'] = round(time.time() - start, 4)
    return stats

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()