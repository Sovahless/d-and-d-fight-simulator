import time
import random
import asyncio
from concurrent.futures import ProcessPoolExecutor
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

# --- DEFINITION DES DONNEES RECUES ---
class StatsEntite(BaseModel):
    hp: int
    ac: int
    init: int
    hit_bonus: int
    avg_dmg: float

class SimulationRequest(BaseModel):
    iterations: int
    pj: StatsEntite
    monstre: StatsEntite

# --- CLASSE INTERNE POUR LE COMBAT ---
class Combattant:
    def __init__(self, stats: dict):
        self.hp = stats['hp']
        self.ac = stats['ac']
        self.init_bonus = stats['init']
        self.hit_bonus = stats['hit_bonus']
        self.avg_dmg = stats['avg_dmg']

    def est_vivant(self):
        return self.hp > 0

    def attaque(self, cible):
        # Jet d'attaque : 1d20 + bonus
        jet = random.randint(1, 20)
        # Note : on simplifie ici (pas de critique natif pour l'instant)
        if jet + self.hit_bonus >= cible.ac:
            # Dégâts : on utilise la moyenne + variation aléatoire légère (+- 20%)
            # pour simuler les dés sans parsing complexe pour l'instant
            variation = random.uniform(0.8, 1.2)
            degats = int(self.avg_dmg * variation)
            # Minimum 1 dégât
            degats = max(1, degats)
            cible.hp -= degats

# --- MOTEUR DE SIMULATION (Exécuté par CPU) ---
def simuler_un_combat(config_data):
    # 1. Création des combattants
    pj = Combattant(config_data['pj'])
    monstre = Combattant(config_data['monstre'])
    
    rounds = 0

    # 2. Initiative
    init_pj = random.randint(1, 20) + pj.init_bonus
    init_m = random.randint(1, 20) + monstre.init_bonus
    
    # Qui commence ? True = PJ, False = Monstre
    tour_pj = init_pj >= init_m

    # 3. Boucle à mort
    while pj.est_vivant() and monstre.est_vivant():
        rounds += 1
        
        # Pour éviter les boucles infinies si personne ne touche jamais
        if rounds > 100: break 

        # On joue deux demi-tours (PJ et Monstre) dans l'ordre de l'initiative
        premier = pj if tour_pj else monstre
        second = monstre if tour_pj else pj

        # Le premier tape
        premier.attaque(second)
        
        # Si le second est toujours vivant, il riposte
        if second.est_vivant():
            second.attaque(premier)

    return {
        "victoire_pj": pj.est_vivant(),
        "rounds": rounds
    }

# --- WRAPPER PARALLELE ---
def lancer_simulation_parallele(payload):
    # CORRECTION ICI : Utilisation de model_dump() pour Pydantic V2
    # Si model_dump n'existe pas (vielle version), on utilise dict()
    try:
        pj_dict = payload.pj.model_dump()
        monstre_dict = payload.monstre.model_dump()
    except AttributeError:
        pj_dict = payload.pj.dict()
        monstre_dict = payload.monstre.dict()

    config = {
        "pj": pj_dict,
        "monstre": monstre_dict
    }
    
    # On utilise tous les cœurs du processeur
    with ProcessPoolExecutor() as executor:
        tous_les_combats = [config] * payload.iterations
        resultats = list(executor.map(simuler_un_combat, tous_les_combats))
    
    victoires = sum(1 for r in resultats if r['victoire_pj'])
    total_rounds = sum(r['rounds'] for r in resultats)
    
    # Sécurité pour éviter la division par zéro
    if payload.iterations == 0:
        return {"iterations": 0, "win_rate": 0, "avg_rounds": 0}

    return {
        "iterations": payload.iterations,
        "win_rate": (victoires / payload.iterations) * 100,
        "avg_rounds": total_rounds / payload.iterations
    }

# --- ROUTES API ---
@app.post("/api/simulate")
async def run_simulation(req: SimulationRequest):
    start_time = time.time()
    
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, lancer_simulation_parallele, req)
    
    stats['calcul_time_sec'] = round(time.time() - start_time, 4)
    return stats

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding='utf-8') as f:
        return f.read()