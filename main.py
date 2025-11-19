import time, random, asyncio, sqlite3, re, json
from math import floor
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()
DB_NAME = "dnd_database.db"

# --- UTILS ---
def roll(dice_str: str):
    if not dice_str: return 0
    try:
        s = str(dice_str).lower().replace(" ", "")
        if 'd' not in s: return int(s)
        total = 0; parts = re.split(r'([+-])', s); sign = 1
        for p in parts:
            if p == '+': sign = 1
            elif p == '-': sign = -1
            elif 'd' in p:
                n_str, f_str = p.split('d')
                total += sum(random.randint(1, int(f_str)) for _ in range(int(n_str) if n_str else 1)) * sign
            elif p.isdigit(): total += int(p) * sign
        return max(0, total)
    except: return 0

def roll_d20(adv: int):
    r1, r2 = random.randint(1, 20), random.randint(1, 20)
    return (max(r1, r2) if adv==1 else (min(r1, r2) if adv==-1 else r1)), True

def get_slots(classe, level):
    # Tables de sorts simplifiées
    idx = min(level, 10) - 1
    if idx < 0: return [0]*5
    FULL = [[2,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,3,0,0],[4,3,3,1,0],[4,3,3,2,0],[4,3,3,3,1],[4,3,3,3,2]]
    HALF = [[0,0,0,0,0],[2,0,0,0,0],[3,0,0,0,0],[3,0,0,0,0],[4,2,0,0,0],[4,2,0,0,0],[4,3,0,0,0],[4,3,0,0,0],[4,3,2,0,0],[4,3,2,0,0]]
    if classe in ["Mage", "Clerc", "Druide", "Barde", "Ensorceleur"]: return list(FULL[idx])
    if classe in ["Paladin", "Rôdeur"]: return list(HALF[idx])
    return [0]*5

# --- DB ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, formule_degats TEXT, type_action TEXT, level INTEGER, save_stat TEXT, effect_json TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS combattants (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, type_entite TEXT, classe TEXT, niveau INTEGER,
        force INTEGER, dexterite INTEGER, constitution INTEGER, intelligence INTEGER, sagesse INTEGER, charisme INTEGER,
        hp_max INTEGER, ac INTEGER, actions_ids TEXT, features TEXT, position TEXT, behavior TEXT
    )''')
    conn.commit(); conn.close()
init_db()

# --- MODELES (Avec ID optionnel pour update) ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str; formule: str; type_action: str; level: int = 0; save_stat: Optional[str] = None; effect_json: Optional[str] = None

class FighterModel(BaseModel):
    id: Optional[int] = None
    nom: str; type_entite: str; classe: str; niveau: int; stats: dict; hp_max: int; ac: int
    actions_ids: List[int]; features: List[str]; position: str = "front"; behavior: str = "random"

class SimuRequest(BaseModel):
    iterations: int; pj_ids: List[int]; monstre_ids: List[int]

# --- MOTEUR SIMULATION ---
class EntiteCombat:
    def __init__(self, data, actions_db):
        self.id = data['id']; self.nom = data['nom']; self.team = data['type_entite']
        self.classe = data['classe']; self.lvl = data['niveau']
        self.stats = {'str': data['force'], 'dex': data['dexterite'], 'con': data['constitution'], 'int': data['intelligence'], 'wis': data['sagesse'], 'cha': data['charisme']}
        self.mods = {k: floor((v-10)/2) for k,v in self.stats.items()}
        self.hp = data['hp_max']; self.hp_max = data['hp_max']; self.base_ac = data['ac']
        ids = json.loads(data['actions_ids']) if data['actions_ids'] else []
        self.actions = [act for act in actions_db if act['id'] in ids]
        self.feats = json.loads(data['features']) if data['features'] else []
        self.position = data['position']; self.behavior = data['behavior']
        self.prof = 2 + floor((self.lvl - 1) / 4)
        self.slots = get_slots(self.classe, self.lvl) if self.team == 'PJ' else [0]*5
        self.effects = []; self.concentrating_on = None; self.init_bonus = self.mods['dex']; self.total_dmg_done = 0

    @property
    def ac(self): return self.base_ac + sum(e['val'] for e in self.effects if e['type'] == 'buff_ac')
    def get_save_mod(self, stat): return self.mods[stat] + self.prof
    def check_concentration(self, dmg):
        if not self.concentrating_on: return False
        dc = max(10, floor(dmg / 2)); save, _ = roll_d20(0)
        if "War Caster" in self.feats: save = max(save, roll_d20(0)[0])
        if (save + self.get_save_mod('con')) < dc:
            self.effects = [e for e in self.effects if not e.get('conc')]; self.concentrating_on = None; return True
        return False
    def start_turn(self):
        for e in self.effects: e['duration'] -= 1
        self.effects = [e for e in self.effects if e['duration'] > 0]
    def has_condition(self, c): return any(e['type'] == c for e in self.effects)
    def choisir_cible(self, enemies):
        if self.behavior == 'focus_low_hp': return min(enemies, key=lambda x: x.hp)
        if self.behavior == 'focus_backline': 
            back = [e for e in enemies if e.position == 'back']
            if back: return random.choice(back)
        return random.choice(enemies)
    def choisir_action(self, ac_avg):
        possible = [a for a in self.actions if a['level']==0 or self.team=='MONSTRE' or self.slots[min(4,a['level']-1)]>0]
        if not possible: return None
        self.use_gwm = "Great Weapon Master" in self.feats and ac_avg < 16
        possible.sort(key=lambda x: x['level'], reverse=True)
        return possible[0] if random.random() < 0.7 else random.choice(possible)

def simuler_bataille(args):
    pj_data, m_data, act_db, log_enabled = args
    pj = [EntiteCombat(d, act_db) for d in pj_data]; mon = [EntiteCombat(d, act_db) for d in m_data]
    tous = pj + mon; rounds = 0; log = []
    def msg(txt): 
        if log_enabled: log.append(txt)

    while any(p.hp>0 for p in pj) and any(m.hp>0 for m in mon):
        rounds += 1
        if rounds > 20: break
        if log_enabled: msg(f"--- TOUR {rounds} ---")
        for e in tous: e.init = random.randint(1,20) + e.init_bonus
        tous.sort(key=lambda x: x.init, reverse=True)
        
        ac_pj = sum(p.ac for p in pj)/len(pj) if pj else 15; ac_m = sum(m.ac for m in mon)/len(mon) if mon else 15

        for actor in tous:
            if actor.hp <= 0 or actor.has_condition('paralyzed'): continue
            actor.start_turn()
            enemies = [e for e in (mon if actor.team=='PJ' else pj) if e.hp>0]
            if not enemies: break
            
            fronts = [e for e in enemies if e.position == 'front']
            target = actor.choisir_cible(fronts if (actor.position=='front' and fronts) else enemies)
            action = actor.choisir_action(ac_m if actor.team=='PJ' else ac_pj)
            if not action: continue

            if action['level'] > 0 and actor.team == 'PJ': actor.slots[min(4, action['level']-1)] -= 1
            eff = json.loads(action['effect_json']) if action.get('effect_json') else None

            if action['type_action'] == 'soin':
                heal = roll(action['formule_degats']); actor.hp = min(actor.hp+heal, actor.hp_max)
                if log_enabled: msg(f"💚 {actor.nom} soigne {heal} PV.")
                if eff and eff['type'].startswith('buff'):
                    actor.effects.append({'type':eff['type'], 'val':roll(eff['val']), 'duration':eff.get('duration',10), 'conc':eff.get('conc',False)})
                    if eff.get('conc'): actor.concentrating_on = True
            else:
                adv = 1 if (target.has_condition('prone') and actor.position=='front') or "Reckless Attack" in actor.feats else 0
                hit = False; dmg = 0; crit = False
                if action['type_action'] == 'save':
                    dc = 8 + actor.prof + actor.mods.get(action.get('save_stat','int'), 0)
                    sv, _ = roll_d20(0); sv_tot = sv + target.get_save_mod(action['save_stat'])
                    if sv_tot < dc: hit = True; dmg = roll(action['formule_degats']); msg(f"🔥 {target.nom} rate save vs {action['nom']}.")
                else:
                    att = max(actor.mods.values()) + actor.prof + (-5 if getattr(actor,'use_gwm',False) else 0)
                    for e in actor.effects: 
                        if e['type']=='buff_atk': att += e['val']
                    d20, _ = roll_d20(adv); crit = (d20==20)
                    if crit or (d20 + att >= target.ac):
                        hit = True; dmg = roll(action['formule_degats']) + max(actor.mods.values()) + (10 if getattr(actor,'use_gwm',False) else 0)
                        if crit: dmg += roll(action['formule_degats'])
                        if log_enabled: msg(f"⚔️ {actor.nom} touche {target.nom} ({d20}+{att}) pour {dmg} dmg.")
                    elif log_enabled: msg(f"💨 {actor.nom} manque {target.nom} ({d20}+{att}).")

                if hit and dmg > 0:
                    target.hp -= dmg; actor.total_dmg_done += dmg
                    if target.check_concentration(dmg) and log_enabled: msg(f"⚠️ {target.nom} perd concentration.")
                    if eff and eff.get('target')=='enemy': target.effects.append({'type':eff['type'], 'duration':eff.get('duration',1), 'val':0})
            if target.hp <= 0 and log_enabled: msg(f"💀 {target.nom} meurt.")

    return {"victoire_pj": any(p.hp>0 for p in pj), "rounds": rounds, "morts": sum(1 for p in pj if p.hp<=0), "log": log, "dmg": {a.nom: a.total_dmg_done for a in pj}}

def process_parallel(payload):
    conn = sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    acts = [dict(r) for r in conn.execute("SELECT * FROM actions").fetchall()]
    pjs = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.pj_ids))})", payload.pj_ids).fetchall()] if payload.pj_ids else []
    ms = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(payload.monstre_ids))})", payload.monstre_ids).fetchall()] if payload.monstre_ids else []
    conn.close()
    if not pjs or not ms: return {"error":"Vide"}
    
    sample = simuler_bataille((pjs, ms, acts, True))
    with ProcessPoolExecutor() as exc:
        res = list(exc.map(simuler_bataille, [(pjs, ms, acts, False)] * (payload.iterations - 1)))
    res.append(sample)
    
    total_dmg = {}
    for r in res:
        if 'dmg' in r:
            for n, v in r['dmg'].items(): total_dmg[n] = total_dmg.get(n, 0) + v

    return {"win_rate": (sum(1 for r in res if r['victoire_pj'])/payload.iterations)*100, "avg_rounds": sum(r['rounds'] for r in res)/payload.iterations, "sample_log": sample['log'], "dmg_distribution": {k: int(v/payload.iterations) for k,v in total_dmg.items()}}

# --- ROUTES API CRUD ---
@app.post("/api/action/save")
def save_action(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    if a.id: # UPDATE
        conn.execute("UPDATE actions SET nom=?, formule_degats=?, type_action=?, level=?, save_stat=?, effect_json=? WHERE id=?",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json, a.id))
    else: # CREATE
        conn.execute("INSERT INTO actions (nom, formule_degats, type_action, level, save_stat, effect_json) VALUES (?,?,?,?,?,?)",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json))
    conn.commit(); conn.close()
    return "ok"

@app.post("/api/fighter/save")
def save_fighter(f: FighterModel):
    conn = sqlite3.connect(DB_NAME)
    f_json = json.dumps(f.features); acts_json = json.dumps(f.actions_ids)
    if f.id: # UPDATE
        conn.execute("""UPDATE combattants SET nom=?, type_entite=?, classe=?, niveau=?, force=?, dexterite=?, constitution=?, intelligence=?, sagesse=?, charisme=?, hp_max=?, ac=?, actions_ids=?, features=?, position=?, behavior=? WHERE id=?""",
        (f.nom, f.type_entite, f.classe, f.niveau, f.stats['str'], f.stats['dex'], f.stats['con'], f.stats['int'], f.stats['wis'], f.stats['cha'], f.hp_max, f.ac, acts_json, f_json, f.position, f.behavior, f.id))
    else: # CREATE
        conn.execute("""INSERT INTO combattants (nom, type_entite, classe, niveau, force, dexterite, constitution, intelligence, sagesse, charisme, hp_max, ac, actions_ids, features, position, behavior) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f.nom, f.type_entite, f.classe, f.niveau, f.stats['str'], f.stats['dex'], f.stats['con'], f.stats['int'], f.stats['wis'], f.stats['cha'], f.hp_max, f.ac, acts_json, f_json, f.position, f.behavior))
    conn.commit(); conn.close()
    return "ok"

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