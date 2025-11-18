import time
import random
import asyncio
import sqlite3
import re
import json
from math import floor
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- OUTILS ---
def calc_mod(score):
    return floor((score - 10) / 2)

def lancer_des_formule(formule: str):
    if not formule: return 0
    formule = formule.lower().replace(" ", "")
    total = 0
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

# --- DB ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT, formule_degats TEXT, type_action TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, 
        intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# --- MODELES & MOTEUR (Identique V5) ---
class ActionModel(BaseModel):
    nom: str
    formule: str
    type_action: str = "attaque"

class FighterModel(BaseModel):
    nom: str
    type_entite: str
    classe: str
    niveau: int
    stats: dict
    hp_max: int
    ac: int
    actions_ids: List[int]

class SimuRequest(BaseModel):
    iterations: int
    pj_ids: List[int]
    monstre_ids: List[int]

class EntiteCombat:
    def __init__(self, data, actions_db):
        self.id = data['id']
        self.nom = data['nom']
        self.team = data['type_entite']
        self.str = data['force']
        self.dex = data['dexterite']
        self.con = data['constitution']
        self.int = data['intelligence']
        self.wis = data['sagesse']
        self.cha = data['charisme']
        self.mod_str, self.mod_dex = calc_mod(self.str), calc_mod(self.dex)
        self.hp = data['hp_max']
        self.hp_max = data['hp_max']
        self.ac = data['ac']
        self.init_bonus = self.mod_dex
        self.init = 0
        ids = json.loads(data['actions_ids']) if data['actions_ids'] else []
        self.actions = [act for act in actions_db if act['id'] in ids]

    def roll_init(self):
        self.init = random.randint(1, 20) + self.init_bonus

    def est_vivant(self):
        return self.hp > 0

    def choisir_action(self):
        if not self.actions: return None
        return random.choice(self.actions)

    def get_attack_bonus(self, action):
        # Proficiency simplifié (+2) + Max(FOR, DEX)
        prof = 2
        return max(self.mod_str, self.mod_dex) + prof

def simuler_bataille(args):
    team_pj_raw, team_m_raw, actions_list = args
    team_pj = [EntiteCombat(d, actions_list) for d in team_pj_raw]
    team_m = [EntiteCombat(d, actions_list) for d in team_m_raw]
    tous = team_pj + team_m
    rounds = 0
    
    while any(p.est_vivant() for p in team_pj) and any(m.est_vivant() for m in team_m):
        rounds += 1
        if rounds > 100: break
        for e in tous: e.roll_init()
        tous.sort(key=lambda x: x.init, reverse=True)
        
        for acteur in tous:
            if not acteur.est_vivant(): continue
            ennemis = [e for e in (team_m if acteur.team == 'PJ' else team_pj) if e.est_vivant()]
            if not ennemis: break
            
            cible = random.choice(ennemis)
            action = acteur.choisir_action()
            
            # Attaque par défaut si pas d'arme
            if not action:
                if random.randint(1, 20) + acteur.mod_str + 2 >= cible.ac:
                    cible.hp -= 1 + acteur.mod_str
                continue

            if action['type_action'] == 'soin':
                heal = lancer_des_formule(action['formule_degats']) + max(0, calc_mod(acteur.wis))
                acteur.hp = min(acteur.hp + heal, acteur.hp_max)
            else:
                bonus_atk = acteur.get_attack_bonus(action)
                jet = random.randint(1, 20)
                if jet == 20:
                    dmg = (lancer_des_formule(action['formule_degats']) * 2) + bonus_atk
                    cible.hp -= dmg
                elif jet + bonus_atk >= cible.ac:
                    dmg = lancer_des_formule(action['formule_degats']) + bonus_atk
                    cible.hp -= max(1, dmg)

    return {
        "victoire_pj": any(p.est_vivant() for p in team_pj),
        "rounds": rounds,
        "morts": sum(1 for p in team_pj if not p.est_vivant())
    }

def process_parallel(payload):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    actions = [dict(r) for r in c.execute("SELECT * FROM actions").fetchall()]
    
    pjs, ms = [], []
    if payload.pj_ids:
        q = f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.pj_ids))})"
        pjs = [dict(r) for r in c.execute(q, payload.pj_ids).fetchall()]
    if payload.monstre_ids:
        q = f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.monstre_ids))})"
        ms = [dict(r) for r in c.execute(q, payload.monstre_ids).fetchall()]
    conn.close()

    if not pjs or not ms: return {"error": "Equipes vides"}

    with ProcessPoolExecutor() as executor:
        args = (pjs, ms, actions)
        results = list(executor.map(simuler_bataille, [args] * payload.iterations))

    victoires = sum(1 for r in results if r['victoire_pj'])
    return {
        "win_rate": (victoires / payload.iterations) * 100,
        "avg_rounds": sum(r['rounds'] for r in results) / payload.iterations,
        "avg_deaths": sum(r['morts'] for r in results) / payload.iterations
    }

# --- API ROUTES ---
@app.post("/api/action/add")
def add_act(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    # On évite les doublons de nom simple
    cur = conn.cursor()
    exist = cur.execute("SELECT id FROM actions WHERE nom = ?", (a.nom,)).fetchone()
    if exist:
        conn.close()
        return {"id": exist[0], "status": "exists"}
    
    cur.execute("INSERT INTO actions (nom, formule_degats, type_action) VALUES (?,?,?)", (a.nom, a.formule, a.type_action))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": new_id, "status": "created"}

@app.get("/api/action/list")
def list_act():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    res = [dict(r) for r in conn.execute("SELECT * FROM actions ORDER BY nom").fetchall()]
    conn.close()
    return res

@app.post("/api/fighter/add")
def add_fighter(f: FighterModel):
    conn = sqlite3.connect(DB_NAME)
    ids = json.dumps(f.actions_ids)
    conn.execute("""INSERT INTO combattants 
        (nom, type_entite, classe, niveau, force, dexterite, constitution, intelligence, sagesse, charisme, hp_max, ac, actions_ids)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f.nom, f.type_entite, f.classe, f.niveau, 
         f.stats['str'], f.stats['dex'], f.stats['con'], f.stats['int'], f.stats['wis'], f.stats['cha'],
         f.hp_max, f.ac, ids))
    conn.commit()
    conn.close()

@app.get("/api/fighter/list")
def list_fighter(type: str):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    res = [dict(r) for r in conn.execute("SELECT * FROM combattants WHERE type_entite=? ORDER BY id DESC", (type,)).fetchall()]
    conn.close()
    return res

@app.post("/api/simulate")
async def sim(req: SimuRequest):
    loop = asyncio.get_running_loop()
    s = time.time()
    res = await loop.run_in_executor(None, process_parallel, req)
    res['time'] = round(time.time() - s, 4)
    return res

@app.get("/", response_class=HTMLResponse)
def root():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()