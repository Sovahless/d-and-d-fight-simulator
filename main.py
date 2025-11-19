import time, random, asyncio, sqlite3, re, json
from math import floor
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
from functools import lru_cache

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- 1. OPTIMISATION : PARSING DES DÉS AVEC CACHE ---
# On décompose le texte UNE fois, on garde le résultat en RAM.
@lru_cache(maxsize=1024)
def parse_dice_string(dice_str: str):
    """Transforme '2d6+4' en tuple (nb_des, faces, bonus)"""
    if not dice_str: return (0, 0, 0)
    s = str(dice_str).lower().replace(" ", "")
    if 'd' not in s:
        try: return (0, 0, int(s))
        except: return (0, 0, 0)
    
    nb_dice = 0
    faces = 0
    bonus = 0
    
    parts = re.split(r'([+-])', s)
    sign = 1
    for p in parts:
        if p == '+': sign = 1
        elif p == '-': sign = -1
        elif 'd' in p:
            try:
                n_str, f_str = p.split('d')
                n = int(n_str) if n_str else 1
                f = int(f_str)
                # On ne gère ici que l'addition simple de dés, 
                # pour des formules complexes, on simplifie
                nb_dice += n * sign # Attention: gestion simplifiée
                faces = f 
            except: pass
        elif p.isdigit():
            bonus += int(p) * sign
            
    return (nb_dice, faces, bonus)

def roll_fast(dice_data):
    """Exécute le jet à partir des données pré-parsées"""
    n, f, b = dice_data
    if n == 0: return b
    # Optimisation mathématique : random.choices est parfois plus lent que la boucle simple sur petits nombres
    # Sur gros volume, sum(random.randint) reste très correct en Python pur
    return sum(random.randint(1, f) for _ in range(n)) + b

def roll_d20_fast(adv: int):
    """ 1=Adv, -1=Disadv, 0=Normal """
    r1 = random.randint(1, 20)
    if adv == 0: return r1, False
    r2 = random.randint(1, 20)
    if adv == 1: return (r1 if r1 > r2 else r2), True
    return (r1 if r1 < r2 else r2), True

# --- 2. TABLES DE PROGRESSION (Pre-computed) ---
FULL_CASTER = [[2,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,3,0,0],[4,3,3,1,0],[4,3,3,2,0],[4,3,3,3,1],[4,3,3,3,2]]
HALF_CASTER = [[0,0,0,0,0],[2,0,0,0,0],[3,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,2,0,0]]

def get_slots(classe, level):
    idx = min(level, 10) - 1
    if idx < 0: return [0]*5
    if classe in ["Mage", "Clerc", "Druide", "Barde", "Ensorceleur"]: return list(FULL_CASTER[idx])
    if classe in ["Paladin", "Rôdeur"]: return list(HALF_CASTER[idx])
    if classe == "Sorcier": return [0,0,2,0,0] if level >= 5 else [0,2,0,0,0]
    return [0]*5

# --- DB INIT ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table actions mise à jour avec 'mastery'
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, formule_degats TEXT, type_action TEXT, level INTEGER, save_stat TEXT, effect_json TEXT, mastery TEXT
    )''')
    # MIGRATION AUTOMATIQUE : On tente d'ajouter la colonne si elle manque
    try:
        c.execute("ALTER TABLE actions ADD COLUMN mastery TEXT")
    except:
        pass # La colonne existe déjà, tout va bien

    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT, behavior TEXT
    )''')
    conn.commit(); conn.close()
init_db()

# --- MODELES ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str
    formule: str
import time, random, asyncio, sqlite3, re, json
from math import floor
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
from functools import lru_cache

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- 1. OPTIMISATION : PARSING DES DÉS AVEC CACHE ---
# On décompose le texte UNE fois, on garde le résultat en RAM.
@lru_cache(maxsize=1024)
def parse_dice_string(dice_str: str):
    """Transforme '2d6+4' en tuple (nb_des, faces, bonus)"""
    if not dice_str: return (0, 0, 0)
    s = str(dice_str).lower().replace(" ", "")
    if 'd' not in s:
        try: return (0, 0, int(s))
        except: return (0, 0, 0)
    
    nb_dice = 0
    faces = 0
    bonus = 0
    
    parts = re.split(r'([+-])', s)
    sign = 1
    for p in parts:
        if p == '+': sign = 1
        elif p == '-': sign = -1
        elif 'd' in p:
            try:
                n_str, f_str = p.split('d')
                n = int(n_str) if n_str else 1
                f = int(f_str)
                # On ne gère ici que l'addition simple de dés, 
                # pour des formules complexes, on simplifie
                nb_dice += n * sign # Attention: gestion simplifiée
                faces = f 
            except: pass
        elif p.isdigit():
            bonus += int(p) * sign
            
    return (nb_dice, faces, bonus)

def roll_fast(dice_data):
    """Exécute le jet à partir des données pré-parsées"""
    n, f, b = dice_data
    if n == 0: return b
    # Optimisation mathématique : random.choices est parfois plus lent que la boucle simple sur petits nombres
    # Sur gros volume, sum(random.randint) reste très correct en Python pur
    return sum(random.randint(1, f) for _ in range(n)) + b

def roll_d20_fast(adv: int):
    """ 1=Adv, -1=Disadv, 0=Normal """
    r1 = random.randint(1, 20)
    if adv == 0: return r1, False
    r2 = random.randint(1, 20)
    if adv == 1: return (r1 if r1 > r2 else r2), True
    return (r1 if r1 < r2 else r2), True

# --- 2. TABLES DE PROGRESSION (Pre-computed) ---
FULL_CASTER = [[2,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,3,0,0],[4,3,3,1,0],[4,3,3,2,0],[4,3,3,3,1],[4,3,3,3,2]]
HALF_CASTER = [[0,0,0,0,0],[2,0,0,0,0],[3,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,2,0,0]]

def get_slots(classe, level):
    idx = min(level, 10) - 1
    if idx < 0: return [0]*5
    if classe in ["Mage", "Clerc", "Druide", "Barde", "Ensorceleur"]: return list(FULL_CASTER[idx])
    if classe in ["Paladin", "Rôdeur"]: return list(HALF_CASTER[idx])
    if classe == "Sorcier": return [0,0,2,0,0] if level >= 5 else [0,2,0,0,0]
    return [0]*5

# --- DB INIT ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table actions mise à jour avec 'mastery'
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, formule_degats TEXT, type_action TEXT, level INTEGER, save_stat TEXT, effect_json TEXT, mastery TEXT
    )''')
    # MIGRATION AUTOMATIQUE : On tente d'ajouter la colonne si elle manque
    try:
        c.execute("ALTER TABLE actions ADD COLUMN mastery TEXT")
    except:
        pass # La colonne existe déjà, tout va bien

    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT, behavior TEXT
    )''')
    conn.commit(); conn.close()
init_db()

# --- MODELES ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str
    formule: str
    type_action: str
    level: int = 0
    save_stat: Optional[str] = None
    effect_json: Optional[str] = None
    mastery: Optional[str] = None  # Nouveau champ pour maîtrise / spécialisation

class FighterModel(BaseModel):
    id: Optional[int] = None
    nom: str; type_entite: str; classe: str; niveau: int; stats: dict; hp_max: int; ac: int
    actions_ids: List[int]; features: List[str]; position: str = "front"; behavior: str = "random"

class SimuRequest(BaseModel):
    iterations: int; pj_ids: List[int]; monstre_ids: List[int]

@app.post("/api/action/save")
def save_action(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    if a.id:
        conn.execute("UPDATE actions SET nom=?, formule_degats=?, type_action=?, level=?, save_stat=?, effect_json=?, mastery=? WHERE id=?",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json, a.mastery, a.id))
    for p in parts:
        if p == '+': sign = 1
        elif p == '-': sign = -1
        elif 'd' in p:
            try:
                n_str, f_str = p.split('d')
                n = int(n_str) if n_str else 1
                f = int(f_str)
                # On ne gère ici que l'addition simple de dés, 
                # pour des formules complexes, on simplifie
                nb_dice += n * sign # Attention: gestion simplifiée
                faces = f 
            except: pass
        elif p.isdigit():
            bonus += int(p) * sign
            
    return (nb_dice, faces, bonus)

def roll_fast(dice_data):
    """Exécute le jet à partir des données pré-parsées"""
    n, f, b = dice_data
    if n == 0: return b
    # Optimisation mathématique : random.choices est parfois plus lent que la boucle simple sur petits nombres
    # Sur gros volume, sum(random.randint) reste très correct en Python pur
    return sum(random.randint(1, f) for _ in range(n)) + b

def roll_d20_fast(adv: int):
    """ 1=Adv, -1=Disadv, 0=Normal """
    r1 = random.randint(1, 20)
    if adv == 0: return r1, False
    r2 = random.randint(1, 20)
    if adv == 1: return (r1 if r1 > r2 else r2), True
    return (r1 if r1 < r2 else r2), True

# --- 2. TABLES DE PROGRESSION (Pre-computed) ---
FULL_CASTER = [[2,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,3,0,0],[4,3,3,1,0],[4,3,3,2,0],[4,3,3,3,1],[4,3,3,3,2]]
HALF_CASTER = [[0,0,0,0,0],[2,0,0,0,0],[3,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,2,0,0]]

def get_slots(classe, level):
    idx = min(level, 10) - 1
    if idx < 0: return [0]*5
    if classe in ["Mage", "Clerc", "Druide", "Barde", "Ensorceleur"]: return list(FULL_CASTER[idx])
    if classe in ["Paladin", "Rôdeur"]: return list(HALF_CASTER[idx])
    if classe == "Sorcier": return [0,0,2,0,0] if level >= 5 else [0,2,0,0,0]
    return [0]*5

# --- DB INIT ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table actions mise à jour avec 'mastery'
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, formule_degats TEXT, type_action TEXT, level INTEGER, save_stat TEXT, effect_json TEXT, mastery TEXT
    )''')
    # MIGRATION AUTOMATIQUE : On tente d'ajouter la colonne si elle manque
    try:
        c.execute("ALTER TABLE actions ADD COLUMN mastery TEXT")
    except:
        pass # La colonne existe déjà, tout va bien

    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT, behavior TEXT
    )''')
    conn.commit(); conn.close()
init_db()

# --- MODELES ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str
    formule: str
import time, random, asyncio, sqlite3, re, json
from math import floor
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
from functools import lru_cache

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- 1. OPTIMISATION : PARSING DES DÉS AVEC CACHE ---
# On décompose le texte UNE fois, on garde le résultat en RAM.
@lru_cache(maxsize=1024)
def parse_dice_string(dice_str: str):
    """Transforme '2d6+4' en tuple (nb_des, faces, bonus)"""
    if not dice_str: return (0, 0, 0)
    s = str(dice_str).lower().replace(" ", "")
    if 'd' not in s:
        try: return (0, 0, int(s))
        except: return (0, 0, 0)
    
    nb_dice = 0
    faces = 0
    bonus = 0
    
    parts = re.split(r'([+-])', s)
    sign = 1
    for p in parts:
        if p == '+': sign = 1
        elif p == '-': sign = -1
        elif 'd' in p:
            try:
                n_str, f_str = p.split('d')
                n = int(n_str) if n_str else 1
                f = int(f_str)
                # On ne gère ici que l'addition simple de dés, 
                # pour des formules complexes, on simplifie
                nb_dice += n * sign # Attention: gestion simplifiée
                faces = f 
            except: pass
        elif p.isdigit():
            bonus += int(p) * sign
            
    return (nb_dice, faces, bonus)

def roll_fast(dice_data):
    """Exécute le jet à partir des données pré-parsées"""
    n, f, b = dice_data
    if n == 0: return b
    # Optimisation mathématique : random.choices est parfois plus lent que la boucle simple sur petits nombres
    # Sur gros volume, sum(random.randint) reste très correct en Python pur
    return sum(random.randint(1, f) for _ in range(n)) + b

def roll_d20_fast(adv: int):
    """ 1=Adv, -1=Disadv, 0=Normal """
    r1 = random.randint(1, 20)
    if adv == 0: return r1, False
    r2 = random.randint(1, 20)
    if adv == 1: return (r1 if r1 > r2 else r2), True
    return (r1 if r1 < r2 else r2), True

# --- 2. TABLES DE PROGRESSION (Pre-computed) ---
FULL_CASTER = [[2,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,3,0,0],[4,3,3,1,0],[4,3,3,2,0],[4,3,3,3,1],[4,3,3,3,2]]
HALF_CASTER = [[0,0,0,0,0],[2,0,0,0,0],[3,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,2,0,0]]

def get_slots(classe, level):
    idx = min(level, 10) - 1
    if idx < 0: return [0]*5
    if classe in ["Mage", "Clerc", "Druide", "Barde", "Ensorceleur"]: return list(FULL_CASTER[idx])
    if classe in ["Paladin", "Rôdeur"]: return list(HALF_CASTER[idx])
    if classe == "Sorcier": return [0,0,2,0,0] if level >= 5 else [0,2,0,0,0]
    return [0]*5

# --- DB INIT ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table actions mise à jour avec 'mastery'
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, formule_degats TEXT, type_action TEXT, level INTEGER, save_stat TEXT, effect_json TEXT, mastery TEXT
    )''')
    # MIGRATION AUTOMATIQUE : On tente d'ajouter la colonne si elle manque
    try:
        c.execute("ALTER TABLE actions ADD COLUMN mastery TEXT")
    except:
        pass # La colonne existe déjà, tout va bien

    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT, behavior TEXT
    )''')
    conn.commit(); conn.close()
init_db()

# --- MODELES ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str
    formule: str
    type_action: str
    level: int = 0
    save_stat: Optional[str] = None
    effect_json: Optional[str] = None
    mastery: Optional[str] = None  # Nouveau champ pour maîtrise / spécialisation

class FighterModel(BaseModel):
    id: Optional[int] = None
    nom: str; type_entite: str; classe: str; niveau: int; stats: dict; hp_max: int; ac: int
    actions_ids: List[int]; features: List[str]; position: str = "front"; behavior: str = "random"

class SimuRequest(BaseModel):
    iterations: int; pj_ids: List[int]; monstre_ids: List[int]

@app.post("/api/action/save")
def save_action(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    if a.id:
        conn.execute("UPDATE actions SET nom=?, formule_degats=?, type_action=?, level=?, save_stat=?, effect_json=?, mastery=? WHERE id=?",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json, a.mastery, a.id))
    else:
        conn.execute("INSERT INTO actions (nom, formule_degats, type_action, level, save_stat, effect_json, mastery) VALUES (?,?,?,?,?,?,?)",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json, a.mastery))
    conn.commit(); conn.close(); return "ok"

@app.post("/api/fighter/save")
def save_fighter(f: FighterModel):
    conn = sqlite3.connect(DB_NAME)
    act_j = json.dumps(f.actions_ids); ft_j = json.dumps(f.features)
    if f.id:
        conn.execute("UPDATE combattants SET nom=?, type_entite=?, classe=?, niveau=?, force=?, dexterite=?, constitution=?, intelligence=?, sagesse=?, charisme=?, hp_max=?, ac=?, actions_ids=?, features=?, position=?, behavior=? WHERE id=?",
        (f.nom, f.type_entite, f.classe, f.niveau, f.stats['str'], f.stats['dex'], f.stats['con'], f.stats['int'], f.stats['wis'], f.stats['cha'], f.hp_max, f.ac, act_j, ft_j, f.position, f.behavior, f.id))
    else:
        conn.execute("INSERT INTO combattants (nom, type_entite, classe, niveau, force, dexterite, constitution, intelligence, sagesse, charisme, hp_max, ac, actions_ids, features, position, behavior) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f.nom, f.type_entite, f.classe, f.niveau, f.stats['str'], f.stats['dex'], f.stats['con'], f.stats['int'], f.stats['wis'], f.stats['cha'], f.hp_max, f.ac, act_j, ft_j, f.position, f.behavior))
    conn.commit(); conn.close(); return "ok"

# --- LOGIQUE SIMULATION ---
class EntiteCombat:
    __slots__ = ('id', 'nom', 'team', 'classe', 'lvl', 'stats', 'mods', 'hp', 'hp_max', 'base_ac', 
                 'actions', 'feats', 'position', 'behavior', 'prof', 'slots', 'effects', 
                 'concentrating_on', 'init_bonus', 'total_dmg_done', 'init', 'use_gwm', 
                 'nb_attacks', 'state', 'death_saves_success', 'death_saves_fail', 'vex_target_id',
                 'damage_taken', 'healing_done', 'crits_dealt', 'misses', 'kills', 'times_downed')

    def __init__(self, data, actions_map):
        self.id = data['id']
        self.nom = data['nom']
        self.team = data['type_entite'] # PJ ou MONSTRE
        self.classe = data['classe']
        self.lvl = data['niveau']
        self.stats = {
            'str': data['force'], 'dex': data['dexterite'], 'con': data['constitution'],
            'int': data['intelligence'], 'wis': data['sagesse'], 'cha': data['charisme']
        }
        self.mods = {k: floor((v - 10) / 2) for k, v in self.stats.items()}
        self.hp_max = data['hp_max']
        self.hp = self.hp_max
        self.base_ac = data['ac']
        self.actions = []
        if data['actions_ids']:
            ids = json.loads(data['actions_ids'])
            for i in ids:
                if i in actions_map: self.actions.append(actions_map[i])
        
        self.feats = json.loads(data['features']) if data['features'] else []
        self.position = data['position']
        self.behavior = data['behavior']
        
        self.prof = 2 + floor((self.lvl - 1) / 4)
        self.slots = get_slots(self.classe, self.lvl)
        self.effects = []
        self.concentrating_on = None
        self.init_bonus = self.mods['dex']
        if 'Initiative' in self.feats: self.init_bonus += 5
        
        self.total_dmg_done = 0
        self.damage_taken = 0
        self.healing_done = 0
        self.crits_dealt = 0
        self.misses = 0
        self.kills = 0
        self.times_downed = 0
        self.init = 0
        self.use_gwm = False
        self.nb_attacks = 1
        if self.lvl >= 5 and self.classe in ['Guerrier', 'Paladin', 'Rôdeur', 'Barbare']: self.nb_attacks = 2
        if self.lvl >= 11 and self.classe == 'Guerrier': self.nb_attacks = 3
        
        self.state = "normal" # normal, prone, stunned, etc.
        self.death_saves_success = 0
        self.death_saves_fail = 0
        self.vex_target_id = None

    @property
    def ac(self):
        base = self.base_ac
        # Shield spell logic could go here if tracked
        return base

    def roll_init(self):
        r, _ = roll_d20_fast(0)
        self.init = r + self.init_bonus

def simuler_bataille(args):
    pj_data, mon_data, actions_map = args
    tous = [EntiteCombat(p, actions_map) for p in pj_data] + [EntiteCombat(m, actions_map) for m in mon_data]
    
    for c in tous: c.roll_init()
    tous.sort(key=lambda x: x.init, reverse=True)
    
    rounds = 0
    log = []
    
    while rounds < 20: # Limit rounds to prevent infinite loops
        rounds += 1
        pj_alive = [c for c in tous if c.team == 'PJ' and c.hp > 0]
        mon_alive = [c for c in tous if c.team == 'MONSTRE' and c.hp > 0]
        
        if not pj_alive or not mon_alive: break
        
        for actor in tous:
            if actor.hp <= 0: continue
            
            # Simple AI: Attack random enemy
            enemies = [e for e in tous if e.team != actor.team and e.hp > 0]
            if not enemies: break
            
            target = random.choice(enemies)
            
            # Choose action (simple: first available attack)
            action = None
            for a in actor.actions:
                if a['type_action'] == 'attaque':
                    action = a
                    break
            
            if action:
                # Attack Roll
                adv = 0
                d20, is_crit = roll_d20_fast(adv)
                att_bonus = actor.mods['str'] + actor.prof # Simplified
                
                hit = False
                crit = (d20 == 20)
                
                if crit or (d20 + att_bonus >= target.ac):
                    hit = True
                    if crit: actor.crits_dealt += 1
                    
                    # Damage Roll
                    dice_data = parse_dice_string(action['formule_degats'])
                    dmg = roll_fast(dice_data) + actor.mods['str']
                    if crit: dmg += roll_fast(dice_data) # Crit adds dice
                    
                    target.hp -= dmg
                    target.damage_taken += dmg
                    actor.total_dmg_done += dmg
                    
                    if target.hp <= 0:
                        actor.kills += 1
                        target.times_downed += 1
                    
                    log.append(f"Round {rounds}: {actor.nom} attaque {target.nom} et inflige {dmg} dégâts.")
                else:
                    actor.misses += 1
                    log.append(f"Round {rounds}: {actor.nom} rate {target.nom}.")
            else:
                 log.append(f"Round {rounds}: {actor.nom} ne fait rien.")

    victoire = any(p.hp > 0 for p in tous if p.team == 'PJ') and not any(m.hp > 0 for m in tous if m.team == 'MONSTRE')
    
    return {
        "victoire_pj": victoire,
        "rounds": rounds,
        "morts": sum(1 for p in tous if p.team == 'PJ' and p.hp <= 0),
        "log": log,
        "dmg": {a.nom: a.total_dmg_done for a in tous if a.team == 'PJ'},
        "fighter_stats": {
            f.nom: {
                "hp_remaining": max(0, f.hp),
                "survived": 1 if f.hp > 0 else 0,
                "dmg_done": f.total_dmg_done,
                "dmg_taken": f.damage_taken,
                "healing_done": f.healing_done,
                "crits_dealt": f.crits_dealt,
                "misses": f.misses,
                "kills": f.kills,
                "times_downed": f.times_downed
            } for f in tous
        }
    }

def process_parallel(payload: SimuRequest):
    conn = sqlite3.connect(DB_NAME); conn.row_factory = sqlite3.Row
    pj_rows = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join(map(str, payload.pj_ids))})").fetchall()]
    mon_rows = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join(map(str, payload.monstre_ids))})").fetchall()]
    actions = {r['id']: dict(r) for r in conn.execute("SELECT * FROM actions").fetchall()}
    conn.close()
    
    # Expand monsters if multiple IDs provided (already handled by frontend sending list of IDs)
    # But we need to handle the case where we want multiple instances of same monster ID? 
    # The frontend sends [id1, id1, id2] so we just need to map them.
    
    final_pj = []
    for pid in payload.pj_ids:
        for row in pj_rows:
            if row['id'] == pid: final_pj.append(row); break
            
    final_mon = []
    for mid in payload.monstre_ids:
        for row in mon_rows:
            if row['id'] == mid: final_mon.append(row); break
            
    args = (final_pj, final_mon, actions)
    
    with ProcessPoolExecutor() as executor:
        results = list(executor.map(simuler_bataille, [args] * payload.iterations))
        
    # Aggregation
    total_dmg = {}
    wins = 0
    tot_rounds = 0
    agg_stats = {}
    
    sample = results[0] if results else {}
    
    for r in results:
        if r['victoire_pj']: wins += 1
        tot_rounds += r['rounds']
        if 'dmg' in r:
            for k, v in r['dmg'].items():
                total_dmg[k] = total_dmg.get(k, 0) + v
        
        if 'fighter_stats' in r:
            for nom, s in r['fighter_stats'].items():
                if nom not in agg_stats:
                    agg_stats[nom] = {"hp_remaining":0, "survived":0, "dmg_done":0, "dmg_taken":0, "healing_done":0, "crits_dealt":0, "misses":0, "kills":0, "times_downed":0}
                agg_stats[nom]["hp_remaining"] += s["hp_remaining"]
                agg_stats[nom]["survived"] += s["survived"]
                agg_stats[nom]["dmg_done"] += s["dmg_done"]
                agg_stats[nom]["dmg_taken"] += s["dmg_taken"]
                agg_stats[nom]["healing_done"] += s["healing_done"]
                agg_stats[nom]["crits_dealt"] += s["crits_dealt"]
                agg_stats[nom]["misses"] += s["misses"]
                agg_stats[nom]["kills"] += s["kills"]
                agg_stats[nom]["times_downed"] += s["times_downed"]

    N = payload.iterations if payload.iterations > 0 else 1
    
    final_stats = {}
    for nom, s in agg_stats.items():
        final_stats[nom] = {
            "avg_hp": int(s["hp_remaining"] / N),
            "survival_rate": int((s["survived"] / N) * 100),
            "avg_dmg_done": int(s["dmg_done"] / N),
            "avg_dmg_taken": int(s["dmg_taken"] / N),
            "avg_healing_done": int(s["healing_done"] / N),
            "avg_crits": round(s["crits_dealt"] / N, 2),
            "avg_misses": round(s["misses"] / N, 2),
            "avg_kills": round(s["kills"] / N, 2),
            "avg_downed": round(s["times_downed"] / N, 2)
        }

    return {
        "win_rate": (wins / N) * 100,
        "avg_rounds": tot_rounds / N,
        "sample_log": sample.get('log', []),
        "dmg_distribution": {k: int(v/N) for k,v in total_dmg.items()},
        "detailed_stats": final_stats
    }

@app.get("/api/action/list")
def list_actions():
    conn=sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM actions ORDER BY level, nom").fetchall()]

@app.get("/api/fighter/list")
def list_fighters(type: str):
    conn=sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM combattants WHERE type_entite=?", (type,)).fetchall()]

@app.post("/api/simulate")
async def run_sim(r: SimuRequest):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, process_parallel, r)

@app.get("/", response_class=HTMLResponse)
def home(): return open("index.html","r",encoding="utf-8").read()