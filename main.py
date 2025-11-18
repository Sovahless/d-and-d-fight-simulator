import time, random, asyncio, sqlite3, re, json
from math import floor, ceil
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- MOTEUR DE DÉS ET REGLES ---
def roll(dice_str: str):
    if not dice_str: return 0
    s = str(dice_str).lower().replace(" ", "")
    total = 0
    parts = re.split(r'([+-])', s)
    sign = 1
    for p in parts:
        if p == '+': sign = 1
        elif p == '-': sign = -1
        elif 'd' in p:
            try:
                n, f = map(int, p.split('d'))
                total += sum(random.randint(1, f) for _ in range(n)) * sign
            except: pass
        else:
            try: total += int(p) * sign
            except: pass
    return max(0, total)

def roll_d20(adv: int):
    """ adv: 1=Avantage, 0=Normal, -1=Désavantage """
    r1, r2 = random.randint(1, 20), random.randint(1, 20)
    if adv == 1: return max(r1, r2)
    if adv == -1: return min(r1, r2)
    return r1

# --- DB ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Actions: ajout 'effect_json' pour définir les buffs/conditions
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT, formule_degats TEXT, type_action TEXT,
        level INTEGER, save_stat TEXT, effect_json TEXT
    )''')
    # Combattants: ajout 'position' (front/back)
    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, 
        intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT
    )''')
    conn.commit(); conn.close()

init_db()

# --- MODELES ---
class ActionModel(BaseModel):
    nom: str; formule: str; type_action: str; level: int = 0; save_stat: Optional[str] = None
    effect_json: Optional[str] = None # Ex: {"type": "buff_atk", "val": "1d4", "conc": true}

class FighterModel(BaseModel):
    nom: str; type_entite: str; classe: str; niveau: int; stats: dict; hp_max: int; ac: int
    actions_ids: List[int]; features: List[str]; position: str = "front"

class SimuRequest(BaseModel):
    iterations: int; pj_ids: List[int]; monstre_ids: List[int]

# --- LOGIQUE INTELLIGENTE ---
class EntiteCombat:
    def __init__(self, data, actions_db):
        self.id = data['id']; self.nom = data['nom']
        self.team = data['type_entite']; self.classe = data['classe']; self.lvl = data['niveau']
        self.stats = {'str': data['force'], 'dex': data['dexterite'], 'con': data['constitution'], 'int': data['intelligence'], 'wis': data['sagesse'], 'cha': data['charisme']}
        self.mods = {k: floor((v-10)/2) for k,v in self.stats.items()}
        self.hp = data['hp_max']; self.hp_max = data['hp_max']; self.base_ac = data['ac']
        
        ids = json.loads(data['actions_ids']) if data['actions_ids'] else []
        self.actions = [act for act in actions_db if act['id'] in ids]
        self.feats = json.loads(data['features']) if data['features'] else []
        self.position = data['position'] # 'front' ou 'back'
        
        self.prof = 2 + floor((self.lvl - 1) / 4)
        # Slots simplifiés (Pool global)
        self.slots = [0]*9
        if self.classe in ["Mage", "Clerc", "Druide", "Sorcier", "Barde"]:
            self.slots = [4, 3, 3, 3, 2] # Simplifié niv 10
        
        # ETATS ET EFFETS
        self.effects = [] # Liste de buffs/conditions actives
        self.concentrating_on = None # ID de l'effet maintenu
        self.init_bonus = self.mods['dex']

    @property
    def ac(self):
        # CA dynamique (ex: +2 si Hâte)
        bonus = sum(e['val'] for e in self.effects if e['type'] == 'buff_ac')
        return self.base_ac + bonus

    def get_save_mod(self, stat):
        bonus = self.mods[stat]
        # Simplification: on donne la proficiency aux saves principaux de la classe
        return bonus + self.prof # On suppose qu'ils sont compétents pour simplifier

    def check_concentration(self, dmg):
        if not self.concentrating_on: return
        dc = max(10, floor(dmg / 2))
        save = roll_d20(0) + self.get_save_mod('con')
        # War Caster feat ? (Avantage) - Non implémenté ici
        if save < dc:
            # Perte de concentration : On retire l'effet sur SOI et sur les ALLIÉS
            # Note: Dans une simu parfaite, il faudrait un lien vers les cibles. 
            # Ici on simplifie: Si je perds la conc, je perds mes propres buffs 'conc'.
            self.effects = [e for e in self.effects if not e.get('conc')]
            self.concentrating_on = None

    def start_turn(self):
        # Decrementer durée effets
        for e in self.effects: e['duration'] -= 1
        self.effects = [e for e in self.effects if e['duration'] > 0]

    def has_condition(self, cond):
        return any(e['type'] == cond for e in self.effects)

    def choisir_action(self, enemies_ac_avg):
        if not self.actions: return None
        possible = []
        for act in self.actions:
            # Check slots
            if act['level'] > 0 and self.team == 'PJ':
                if self.slots[min(4, act['level']-1)] <= 0: continue
            possible.append(act)
        
        if not possible: return None
        
        # LOGIQUE GWM / SHARPSHOOTER
        # Si j'ai le feat, et que la CA ennemie est faible (< 16), j'active le mode Power Attack
        self.use_gwm = False
        if "Great Weapon Master" in self.feats and enemies_ac_avg < 16:
            self.use_gwm = True

        possible.sort(key=lambda x: x['level'], reverse=True)
        return possible[0] if random.random() < 0.8 else random.choice(possible)

def simuler_bataille(args):
    pj_data, m_data, act_db = args
    pj = [EntiteCombat(d, act_db) for d in pj_data]
    mon = [EntiteCombat(d, act_db) for d in m_data]
    tous = pj + mon
    rounds = 0
    
    while any(p.hp>0 for p in pj) and any(m.hp>0 for m in mon):
        rounds += 1
        if rounds > 30: break
        
        for e in tous: e.init = random.randint(1,20) + e.init_bonus
        tous.sort(key=lambda x: x.init, reverse=True)
        
        # Calcul CA moyenne adverse pour l'IA
        ac_pj_avg = sum(p.ac for p in pj)/len(pj) if pj else 15
        ac_m_avg = sum(m.ac for m in mon)/len(mon) if mon else 15

        for actor in tous:
            if actor.hp <= 0: continue
            if actor.has_condition('paralyzed') or actor.has_condition('unconscious'): continue
            
            actor.start_turn()
            
            enemies = [e for e in (mon if actor.team=='PJ' else pj) if e.hp>0]
            allies = [a for a in (pj if actor.team=='PJ' else mon) if a.hp>0]
            if not enemies: break

            # --- POSITIONNEMENT ---
            # Si je suis Melee, je tape n'importe qui.
            # Si je suis Range, je tape n'importe qui.
            # MAIS: Si je suis un ennemi Melee, je ne peux taper la Backline que si la Frontline est morte.
            frontline_enemies = [e for e in enemies if e.position == 'front']
            valid_targets = frontline_enemies if frontline_enemies else enemies
            target = random.choice(valid_targets)

            action = actor.choisir_action(ac_m_avg if actor.team=='PJ' else ac_pj_avg)
            if not action: continue

            # Consommer Slot
            if action['level'] > 0 and actor.team == 'PJ':
                lvl_idx = min(4, action['level']-1)
                actor.slots[lvl_idx] -= 1

            # --- PARSING EFFETS DU SORT ---
            eff_data = json.loads(action['effect_json']) if action.get('effect_json') else None

            # 1. BUFF (Soin ou Stat)
            if action['type_action'] == 'soin' or (eff_data and eff_data.get('target') == 'self'):
                # Cible = Soi ou Allié blessé
                receiver = actor
                if action['type_action'] == 'soin':
                    heal = roll(action['formule_degats'])
                    receiver.hp = min(receiver.hp + heal, receiver.hp_max)
                
                # Appliquer Buff (ex: Haste, Bless)
                if eff_data and eff_data['type'].startswith('buff'):
                    buff = {
                        'type': eff_data['type'], # ex: 'buff_ac'
                        'val': int(eff_data['val']) if str(eff_data['val']).isdigit() else 0, 
                        'dice': eff_data['val'] if 'd' in str(eff_data['val']) else None,
                        'duration': eff_data.get('duration', 10),
                        'conc': eff_data.get('conc', False)
                    }
                    receiver.effects.append(buff)
                    if buff['conc']: actor.concentrating_on = True

            # 2. OFFENSIF (Attaque ou Save)
            else:
                # CALCUL AVANTAGE / DESAVANTAGE
                adv = 0
                # Règle : Tirer à distance (Back) sur un ennemi au CaC (Front) sans être menacé = Normal
                # Règle : Attaquer un ennemi 'Prone' au CaC = Avantage
                if target.has_condition('prone') and actor.position == 'front': adv = 1
                if target.has_condition('blinded'): adv = 1
                if actor.has_condition('blinded'): adv = -1
                
                # Règle : Reckless Attack (Barbare)
                if "Reckless Attack" in actor.feats and actor.position == 'front': adv = 1

                hit = False
                crit = False
                dmg = 0

                if action['type_action'] == 'save':
                    dc = 8 + actor.prof + actor.mods.get(action.get('save_stat', 'int'), 0)
                    save_roll = roll_d20(0) + target.get_save_mod(action['save_stat'])
                    if save_roll < dc:
                        hit = True
                        dmg = roll(action['formule_degats'])
                else:
                    # ATTAQUE
                    att_mod = max(actor.mods.values()) + actor.prof
                    
                    # Bonus BLESS (+1d4)
                    for e in actor.effects: 
                        if e['type'] == 'buff_atk' and e['dice']: att_mod += roll(e['dice'])

                    # Malus GWM (-5)
                    if getattr(actor, 'use_gwm', False): att_mod -= 5

                    d20 = roll_d20(adv)
                    if d20 == 20: crit = True
                    if d20 == 20 or (d20 + att_mod >= target.ac): hit = True
                    
                    if hit:
                        dmg = roll(action['formule_degats']) + max(actor.mods.values())
                        if crit: dmg += roll(action['formule_degats'])
                        if getattr(actor, 'use_gwm', False): dmg += 10 # Bonus GWM

                if hit and dmg > 0:
                    target.hp -= dmg
                    target.check_concentration(dmg) # Test de CON pour maintenir ses sorts
                    
                    # Appliquer Debuff (ex: Renversement, Poison)
                    if eff_data and eff_data.get('target') == 'enemy':
                        debuff = {'type': eff_data['type'], 'duration': eff_data.get('duration', 1), 'val':0}
                        target.effects.append(debuff)

    return {"victoire_pj": any(p.hp>0 for p in pj), "rounds": rounds, "morts": sum(1 for p in pj if p.hp<=0)}

def process_parallel(payload):
    conn = sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row; c=conn.cursor()
    acts = [dict(r) for r in c.execute("SELECT * FROM actions").fetchall()]
    pjs = [dict(r) for r in c.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.pj_ids))})", payload.pj_ids).fetchall()] if payload.pj_ids else []
    ms = [dict(r) for r in c.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.monstre_ids))})", payload.monstre_ids).fetchall()] if payload.monstre_ids else []
    conn.close()
    if not pjs or not ms: return {"error":"Vide"}
    with ProcessPoolExecutor() as exc:
        res = list(exc.map(simuler_bataille, [(pjs, ms, acts)]*payload.iterations))
    return {
        "win_rate": (sum(1 for r in res if r['victoire_pj'])/payload.iterations)*100,
        "avg_rounds": sum(r['rounds'] for r in res)/payload.iterations,
        "avg_deaths": sum(r['morts'] for r in res)/payload.iterations
    }

# --- ROUTES API ---
@app.post("/api/action/add")
def add_a(a: ActionModel):
    conn=sqlite3.connect(DB_NAME)
    # Parsing JSON simple pour le frontend
    eff = a.effect_json
    conn.execute("INSERT INTO actions (nom,formule_degats,type_action,level,save_stat,effect_json) VALUES (?,?,?,?,?,?)",
                 (a.nom, a.formule, a.type_action, a.level, a.save_stat, eff))
    conn.commit(); conn.close()

@app.get("/api/action/list")
def list_a():
    conn=sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM actions ORDER BY level, nom").fetchall()]

@app.post("/api/fighter/add")
def add_f(f: FighterModel):
    conn=sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO combattants (nom,type_entite,classe,niveau,force,dexterite,constitution,intelligence,sagesse,charisme,hp_max,ac,actions_ids,features,position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (f.nom,f.type_entite,f.classe,f.niveau,f.stats['str'],f.stats['dex'],f.stats['con'],f.stats['int'],f.stats['wis'],f.stats['cha'],f.hp_max,f.ac,json.dumps(f.actions_ids),json.dumps(f.features), f.position))
    conn.commit(); conn.close()

@app.get("/api/fighter/list")
def list_f(type:str):
    conn=sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM combattants WHERE type_entite=?",(type,)).fetchall()]

@app.post("/api/simulate")
async def sim(r: SimuRequest):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, process_parallel, r)

@app.get("/", response_class=HTMLResponse)
def home(): return open("index.html","r",encoding="utf-8").read()