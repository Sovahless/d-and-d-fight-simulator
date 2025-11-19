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

# --- MODELES ---
class ActionModel(BaseModel):
    id: Optional[int] = None
    nom: str; formule: str; type_action: str; level: int = 0; save_stat: Optional[str] = None; effect_json: Optional[str] = None

class FighterModel(BaseModel):
    id: Optional[int] = None
    nom: str; type_entite: str; classe: str; niveau: int; stats: dict; hp_max: int; ac: int
    actions_ids: List[int]; features: List[str]; position: str = "front"; behavior: str = "random"

class SimuRequest(BaseModel):
    iterations: int; pj_ids: List[int]; monstre_ids: List[int]

# --- MOTEUR OPTIMISÉ ---
class EntiteCombat:
    # OPTIMISATION __slots__ : Réduit drastiquement l'empreinte mémoire et accélère l'accès aux attributs
    __slots__ = ('id', 'nom', 'team', 'classe', 'lvl', 'stats', 'mods', 'hp', 'hp_max', 'base_ac', 
                 'actions', 'feats', 'position', 'behavior', 'prof', 'slots', 'effects', 
                 'concentrating_on', 'init_bonus', 'total_dmg_done', 'init', 'use_gwm', 
                 'nb_attacks', 'state', 'death_saves_success', 'death_saves_fail')

    def __init__(self, data, actions_map):
        self.id = data['id']; self.nom = data['nom']; self.team = data['type_entite']
        self.classe = data['classe']; self.lvl = data['niveau']
        
        # Stats brutes
        self.stats = (data['force'], data['dexterite'], data['constitution'], data['intelligence'], data['sagesse'], data['charisme'])
        # Mods pré-calculés (Tuple pour accès rapide par index: 0=STR, 1=DEX...)
        self.mods = {'str': floor((self.stats[0]-10)/2), 'dex': floor((self.stats[1]-10)/2), 'con': floor((self.stats[2]-10)/2), 
                     'int': floor((self.stats[3]-10)/2), 'wis': floor((self.stats[4]-10)/2), 'cha': floor((self.stats[5]-10)/2)}
        
        self.hp = data['hp_max']; self.hp_max = data['hp_max']; self.base_ac = data['ac']
        
        # Lookup rapide des actions via la Map (O(1)) au lieu de boucler
        ids = json.loads(data['actions_ids']) if data['actions_ids'] else []
        self.actions = [actions_map[i] for i in ids if i in actions_map]
        
        # Pre-parse dice for actions to avoid regex at runtime
        for a in self.actions:
            if 'parsed_dice' not in a:
                a['parsed_dice'] = parse_dice_string(a['formule_degats'])
                # Pre-parse effects too
                if a.get('effect_json'):
                    try:
                        eff = json.loads(a['effect_json'])
                        if eff.get('val') and isinstance(eff['val'], str) and 'd' in eff['val']:
                             eff['parsed_val'] = parse_dice_string(eff['val'])
                        else:
                             eff['parsed_val'] = (0,0, int(eff['val']) if str(eff['val']).isdigit() else 0)
                        a['parsed_effect'] = eff
                    except: a['parsed_effect'] = None
                else: a['parsed_effect'] = None

        self.feats = set(json.loads(data['features'])) if data['features'] else set()
        self.position = data['position']; self.behavior = data['behavior']
        self.prof = 2 + floor((self.lvl - 1) / 4)
        self.slots = get_slots(self.classe, self.lvl) if self.team == 'PJ' else [0]*5
        self.effects = []; self.concentrating_on = None; self.init_bonus = self.mods['dex']; self.total_dmg_done = 0
        self.init = 0
        self.use_gwm = False
        # --- MORTS & DEATH SAVES ---
        self.death_saves_success = 0
        self.death_saves_fail = 0
        self.state = 'alive' # 'alive', 'downed', 'dead'

        # --- CORRECTION 2A : CALCUL EXTRA ATTACK ---
        self.nb_attacks = 1
        # Note : Le Moine n'est pas dans votre liste HTML mais devrait y être.
        martial_classes = ["Guerrier", "Barbare", "Paladin", "Rôdeur", "Moine"] 
        if self.classe in martial_classes and self.lvl >= 5:
            self.nb_attacks = 2
        if self.classe == "Guerrier" and self.lvl >= 11: # Le Guerrier a une 3ème attaque
            self.nb_attacks = 3

    @property
    def ac(self):
        # Optimisation : generator expression rapide
        return self.base_ac + sum(e['val'] for e in self.effects if e['type'] == 'buff_ac')

    def get_save_mod(self, stat):
        return self.mods.get(stat, 0) + self.prof

    def check_concentration(self, dmg):
        if not self.concentrating_on: return False
        dc = max(10, floor(dmg / 2))
        save, _ = roll_d20_fast(0)
        if "War Caster" in self.feats: save = max(save, roll_d20_fast(0)[0])
        
        if (save + self.get_save_mod('con')) < dc:
            self.effects = [e for e in self.effects if not e.get('conc')]
            self.concentrating_on = None
            return True
        return False

    def start_turn(self):
        # List comprehension est plus rapide que boucle remove
        self.effects = [e for e in self.effects if (e.update({'duration': e['duration']-1}) or True) and e['duration'] >= 0]
        # --- DEATH SAVES ---
        if self.state == 'downed':
            d20 = random.randint(1, 20)
            if d20 == 20:
                self.state = 'alive'
                self.hp = 1
                self.death_saves_success = 0
                self.death_saves_fail = 0
            elif d20 == 1:
                self.death_saves_fail += 2
            elif d20 >= 10:
                self.death_saves_success += 1
            else:
                self.death_saves_fail += 1
            # Stable
            if self.death_saves_success >= 3:
                self.state = 'stable'
            # Mort définitive
            if self.death_saves_fail >= 3:
                self.state = 'dead'
                self.hp = -1

    def has_condition(self, c):
        for e in self.effects:
            if e['type'] == c: return True
        return False

    def choisir_cible(self, enemies):
        if not enemies: return None
        if self.behavior == 'focus_low_hp': return min(enemies, key=lambda x: x.hp)
        if self.behavior == 'focus_backline': 
            back = [e for e in enemies if e.position == 'back']
            if back: return random.choice(back)
        return random.choice(enemies)

    def choisir_action(self, ac_avg):
        # Filtrage optimisé
        candidates = []
        for a in self.actions:
            if a['level'] == 0: candidates.append(a)
            elif self.team == 'MONSTRE': candidates.append(a)
            else:
                # Check slot
                idx = min(4, a['level']-1)
                if self.slots[idx] > 0: candidates.append(a)
        
        if not candidates: return None
        
        self.use_gwm = "Great Weapon Master" in self.feats and ac_avg < 16
        # Sort simple pour l'IA (Privilégie haut niveau)
        candidates.sort(key=lambda x: x['level'], reverse=True)
        
        # Petit random pour ne pas être robotique
        return candidates[0] if (len(candidates)==1 or random.random() < 0.7) else candidates[1]

def simuler_bataille(args):
    pj_data, m_data, act_map, log_enabled = args
    
    # Création des objets (Coûteux, mais nécessaire car l'état change)
    pj = [EntiteCombat(d, act_map) for d in pj_data]
    mon = [EntiteCombat(d, act_map) for d in m_data]
    tous = pj + mon
    
    rounds = 0
    log = []
    def msg(t): 
        if log_enabled: log.append(t)

    while any(p.hp > 0 for p in pj) and any(m.hp > 0 for m in mon):
        rounds += 1
        if rounds > 200: break
        if log_enabled: msg(f"--- TOUR {rounds} ---")
        
        # Roll init only once per round (Simplification 5e standard)
        for e in tous: 
            e.init = random.randint(1,20) + e.init_bonus
        tous.sort(key=lambda x: x.init, reverse=True)

        while any(p.hp > 0 for p in pj) and any(m.hp > 0 for m in mon):
            rounds += 1
            if rounds > 200: break
            if log_enabled: msg(f"--- TOUR {rounds} ---")
        
        # Pre-calc averages
        ac_pj = sum(p.ac for p in pj)/len(pj) if pj else 10
        ac_m = sum(m.ac for m in mon)/len(mon) if mon else 10

        for actor in tous:
            if actor.hp <= 0: continue
            # Check condition rapide
            if actor.has_condition('paralyzed'): continue
            
            actor.start_turn()
            
            # Selection Cible Optimisée
            is_pj = (actor.team == 'PJ')
            targets = mon if is_pj else pj
            active_targets = [e for e in targets if e.hp > 0]
            
            if not active_targets: break
            
            # Frontline logic
            fronts = [e for e in active_targets if e.position == 'front']
            valid = fronts if (actor.position=='front' and fronts) else active_targets
            
            # Sélection de la cible principale
            target = actor.choisir_cible(valid)
            if not target: continue

            # Choix de l'action
            action = actor.choisir_action(ac_m if is_pj else ac_pj)

            # AJOUTEZ CECI POUR DEBUGGER
            if not action:
                # On affiche un message seulement au tour 1 pour ne pas spammer
                if rounds == 1 and log_enabled:
                    msg(f"⚠️ {actor.nom} passe son tour (aucune action valide trouvée !)")
                continue

            # --- LOGIQUE AOE ---
            targets_list = [target] # Par défaut, une seule cible
            eff = action.get('parsed_effect')
            if eff and eff.get('aoe'):
                targets_list = [e for e in active_targets]
                if log_enabled: msg(f"💥 {actor.nom} lance {action['nom']} sur {len(targets_list)} cibles !")

            # Pay Slot (une seule fois)
            if action['level'] > 0 and is_pj:
                actor.slots[min(4, action['level']-1)] -= 1

            # --- RÉSOLUTION POUR CHAQUE CIBLE ---
            for t in targets_list:
                if t.state == 'dead': continue
                if t.hp <= 0 and t.state != 'downed':
                    t.hp = 0
                    t.state = 'downed'
                    if log_enabled: msg(f"☠️ {t.nom} tombe à terre (downed) !")
                if t.state == 'downed' or t.state == 'stable':
                    # Seul le soin peut relever
                    if action['type_action'] == 'soin':
                        heal = roll_fast(action['parsed_dice'])
                        t.hp = min(t.hp+heal, t.hp_max)
                        t.state = 'alive'
                        t.death_saves_success = 0
                        t.death_saves_fail = 0
                        if log_enabled: msg(f"💚 {actor.nom} relève {t.nom} avec {heal} PV !")
                        if eff and eff['type'].startswith('buff'):
                            val_roll = roll_fast(eff['parsed_val'])
                            t.effects.append({'type':eff['type'], 'val':val_roll, 'duration':eff.get('duration',10), 'conc':eff.get('conc',False)})
                            if eff.get('conc'): t.concentrating_on = True
                    continue
                if action['type_action'] == 'soin':
                    heal = roll_fast(action['parsed_dice'])
                    actor.hp = min(actor.hp+heal, actor.hp_max)
                    if log_enabled: msg(f"💚 {actor.nom} soigne {heal}.")
                    if eff and eff['type'].startswith('buff'):
                        val_roll = roll_fast(eff['parsed_val'])
                        actor.effects.append({'type':eff['type'], 'val':val_roll, 'duration':eff.get('duration',10), 'conc':eff.get('conc',False)})
                        if eff.get('conc'): actor.concentrating_on = True
                else:
                    adv = 0
                    if actor.position == 'front':
                        if t.has_condition('prone'): adv = 1
                        if "Reckless Attack" in actor.feats: adv = 1
                    hit = False; dmg = 0; crit = False
                    if action['type_action'] == 'save':
                        dc = 8 + actor.prof + actor.mods.get(action.get('save_stat','int'), 0)
                        sv, _ = roll_d20_fast(0)
                        if (sv + t.get_save_mod(action['save_stat'])) < dc:
                            hit = True
                            dmg = roll_fast(action['parsed_dice'])
                            if log_enabled: msg(f"🔥 {t.nom} rate save vs {action['nom']}.")
                    else:
                        iter_attaques = actor.nb_attacks if action['type_action'] == 'attaque' else 1
                        for i in range(iter_attaques):
                            if t.hp <= 0: break
                            has_adv = False
                            has_dis = False
                            if actor.position == 'front':
                                if t.has_condition('prone'): has_adv = True
                                if "Reckless Attack" in actor.feats: has_adv = True
                                if t.has_condition('blinded'): has_adv = True
                            if actor.has_condition('blinded'): has_dis = True
                            adv = 0
                            if has_adv and not has_dis: adv = 1
                            elif has_dis and not has_adv: adv = -1
                            att = max(actor.mods.values()) + actor.prof
                            if actor.use_gwm: att -= 5
                            for e in actor.effects:
                                if e['type'] == 'buff_atk': att += e['val']
                            d20, has_adv_bool = roll_d20_fast(adv)
                            crit = (d20 == 20)
                            if crit or (d20 + att >= t.ac):
                                hit = True
                                dmg = roll_fast(action['parsed_dice'])
                                if crit:
                                    n, f, _ = action['parsed_dice']
                                    dmg += roll_fast((n, f, 0))
                                if log_enabled: msg(f"⚔️ {actor.nom} crit! Dégâts totaux: {dmg}")
                                t.hp -= dmg
                                actor.total_dmg_done += dmg
                                if t.check_concentration(dmg) and log_enabled: msg(f"⚠️ {t.nom} perd conc.")
                                if eff and eff.get('target')=='enemy':
                                    t.effects.append({'type':eff['type'], 'duration':eff.get('duration',1), 'val':0})
                            elif log_enabled: msg(f"💨 {actor.nom} manque ({d20}+{att}).")
                # Check mort APRÈS la boucle d'attaques (ligne 169 originale)
                if t.hp <= 0 and t.state == 'alive':
                    t.hp = 0
                    t.state = 'downed'
                    if log_enabled: msg(f"☠️ {t.nom} tombe à terre (downed) !")
                if t.state == 'dead' and log_enabled:
                    msg(f"💀 {t.nom} meurt définitivement.")

    # Return stats
    # CORRECTION : On ne gagne que si on est en vie ET que les ennemis sont morts
    victoire = any(p.hp > 0 for p in pj) and not any(m.hp > 0 for m in mon)

    return {
        "victoire_pj": victoire,
        "rounds": rounds,
        "morts": sum(1 for p in pj if p.hp <= 0),
        "log": log,
        "dmg": {a.nom: a.total_dmg_done for a in pj}
    }

def process_parallel(payload):
    # Lecture DB Optimisée (Dict Lookup)
    conn = sqlite3.connect(DB_NAME); conn.row_factory=sqlite3.Row
    # On charge les actions
    actions_list = [dict(r) for r in conn.execute("SELECT * FROM actions").fetchall()]
    act_map = {a['id']: a for a in actions_list}
    # --- MODIFICATION GESTION QUANTITÉS ---
    # 1. Récupérer les données UNIQUES depuis la DB pour éviter les doublons SQL
    unique_pj_ids = list(set(payload.pj_ids))
    unique_mon_ids = list(set(payload.monstre_ids))
    pj_rows = []
    if unique_pj_ids:
        pj_rows = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(unique_pj_ids))})", unique_pj_ids).fetchall()]
    mon_rows = []
    if unique_mon_ids:
        mon_rows = [dict(r) for r in conn.execute(f"SELECT * FROM combattants WHERE id IN ({','.join('?'*len(unique_mon_ids))})", unique_mon_ids).fetchall()]
    conn.close()
    # 2. Créer un dictionnaire pour accès rapide {id: data}
    pj_map = {row['id']: row for row in pj_rows}
    mon_map = {row['id']: row for row in mon_rows}
    # 3. Reconstruire la liste complète avec les DUPLICATIONS demandées par le frontend
    pjs = [pj_map[i] for i in payload.pj_ids if i in pj_map]
    ms = [mon_map[i] for i in payload.monstre_ids if i in mon_map]
    # --- FIN MODIFICATION ---
    if not pjs or not ms: return {"error":"Vide"}
    # Le reste de la fonction reste identique...
    sample = simuler_bataille((pjs, ms, act_map, True))
    with ProcessPoolExecutor() as exc:
        results = list(exc.map(simuler_bataille, [(pjs, ms, act_map, False)] * (payload.iterations - 1)))
    results.append(sample)
    # Aggregation
    total_dmg = {}
    wins = 0
    tot_rounds = 0
    for r in results:
        if r['victoire_pj']: wins += 1
        tot_rounds += r['rounds']
        if 'dmg' in r:
            for k, v in r['dmg'].items():
                total_dmg[k] = total_dmg.get(k, 0) + v
    N = payload.iterations
    return {
        "win_rate": (wins / N) * 100,
        "avg_rounds": tot_rounds / N,
        "sample_log": sample['log'],
        "dmg_distribution": {k: int(v/N) for k,v in total_dmg.items()}
    }

# --- ROUTES API ---
@app.post("/api/action/save")
def save_action(a: ActionModel):
    conn = sqlite3.connect(DB_NAME)
    if a.id:
        conn.execute("UPDATE actions SET nom=?, formule_degats=?, type_action=?, level=?, save_stat=?, effect_json=? WHERE id=?",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json, a.id))
    else:
        conn.execute("INSERT INTO actions (nom, formule_degats, type_action, level, save_stat, effect_json) VALUES (?,?,?,?,?,?)",
                     (a.nom, a.formule, a.type_action, a.level, a.save_stat, a.effect_json))
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