# Studio IA - conception et plan

Objectif: logiciel généraliste local, de l'idée ou du script au MP4, avec scènes,
plans courts, images, prises conservées, choix explicite, approbation, reprise et
assemblage. Moteur V1: MiniMax H3 / FastH3 sur Modal. Aucun contenu imposé.

Base retenue: Calliope MIT, commit caea320 (la révision complète figure dans
THIRD_PARTY_NOTICES.md). Réutiliser sa base SQLite, ses projets/scènes/clips, sa
file de travaux, ses assets et son client LLM. Ajouter une surface /studio simple
en français. Les écrans avancés Calliope restent accessibles.

## Modifications

- [x] Installer et exécuter les tests existants avant modifications.
- [x] Ajouter studio/schema.py et studio/service.py: prises, provenance, sélection,
  approbation et exports. Ne pas remplacer la prise choisie lors d'un rerender.
- [x] Ajouter routers/studio.py: configuration, préparation, board, génération,
  import, choix, annulation, export. Monter ce routeur dans main.py.
- [x] Brancher la file existante sur studio/service.py pour les jobs Studio.
  Conserver les identifiants Modal, reprendre la même FunctionCall au redémarrage.
- [x] Ajouter engines/modal_h3.py et deploy/modal_h3.py: moteur isolé et workflow
  FastH3 existant, volume fasth3-models, nouveau déploiement indépendant du service
  newsreel-fasth3. T2V, I2V, audio natif, formats paysage/portrait/carré.
- [x] Ajouter /studio, /studio/[id] et composants dédiés: projets, planification,
  scènes/plans, galerie image/vidéo, choix/approbation, état et MP4 final.
- [x] Fournir launch.ps1, launch.cmd et un exemple, sans configuration à éditer
  dans le code. Services et clés configurables dans l'interface.
- [x] Tester le parcours réel, les interruptions, les prises, la persistance et
  le MP4 FFmpeg. Capturer les résultats effectivement validés.
- [x] Commits ciblés, dépôt privé et branche poussée, pas de merge.

## Invariants

Les projets/scènes/plans utilisent les tables Calliope. Les tables Studio ajoutent
les prises et leur sélection. Les paramètres effectifs sont figés au lancement.
Une vidéo conserve sa prise image source et l'identifiant de son appel Modal.
La création d'une nouvelle prise ne sélectionne jamais automatiquement la vidéo.
Changer une référence ou un prompt invalide l'approbation; l'historique reste.
L'export requiert tous les plans choisis et approuvés et fige leur ordre/fichiers.
L'export est réellement H.264/AAC. Aucun placeholder ne vaut test de génération.
Les clés restent locales et sont masquées dans les réponses et ignorées par Git.

## Contrat de l'interface

Toutes les routes ci-dessous ont le préfixe /api/studio.
GET /config -> {modal_app, modal_function, llm_base_url, llm_model,
llm_configured, image_base_url, image_model, image_configured, dry_run}.
PATCH /config accepte ces champs et llm_api_key/image_api_key (jamais renvoyées).
GET /preflight -> {modal: {ok, message}, ffmpeg: {ok, message},
llm: {ok, message}, image: {ok, message}, engine}.
GET /projects -> {projects: [{id,title,idea,aspect_ratio,scene_count,shot_count,
approved_count,created_at,updated_at,cover_url}]}.
POST /projects {title,idea,aspect_ratio: '16:9'|'9:16'|'1:1'} -> projet.
GET /projects/{id} -> {project,scenes,jobs,exports}; chaque scène contient shots.
Chaque shot: {id,scene_id,order_index,description,prompt,image_prompt,dialogue,
duration_sec,trim_start_sec,trim_end_sec,audio_mode:'native'|'silent',
selected_image_id,selected_video_id,
approved,takes}. Chaque take: {id,clip_id,kind:'image'|'video',status,
url,prompt,seed,error,job_id,source_take_id,created_at,metadata}.
POST /projects/{id}/plan {text,mode:'script'|'idea',use_llm:boolean} -> board.
GET /projects/{id}/example -> non utilisé; POST /examples crée le projet exemple.
POST /projects/{id}/scenes {heading,action,dialog} -> scène et plan initial.
PATCH /projects/{id}/scenes/{scene_id} {heading,action,dialog} -> scène.
POST /projects/{id}/scenes/{scene_id}/shots {description,prompt,duration_sec} -> plan.
PATCH /projects/{id}/shots/{shot_id} {description,prompt,image_prompt,dialogue,
duration_sec,audio_mode,order_index,trim_start_sec,trim_end_sec} -> plan.
duration_sec est la durée utile écrite (1 à 15 s, libre) ; trim_start_sec et
trim_end_sec bornent la partie conservée d'une prise (null = coupe automatique
au montage, alignée sur la grille 24 fps). Le montage ne rallonge jamais une
prise : seule la partie utile entre dans le film.
DELETE /projects/{id}/shots/{shot_id} -> {ok:true} (au moins un plan par scène).
POST /projects/{id}/reorder {scene_ids?,shot_ids?,scene_id?} -> board.
POST /shots/{id}/generate {kind,request_id,n_takes:1..4,seed?} -> {takes,jobs}.
POST /shots/{id}/upload multipart file + kind=image|video -> take.
POST /takes/{id}/select -> shot. POST /shots/{id}/approve {approved:boolean} -> shot.
POST /jobs/{id}/cancel -> job. POST /jobs/{id}/resume -> job.
POST /projects/{id}/export {request_id} -> {job_id}.
Les projets et leurs médias persistent localement dans calliope-backend/data.
Médias locaux servis par /api/file?path=... existant (fichiers du data_dir).

## Tests prioritaires

Prises conservées après rerender; double clic dédupliqué; référence changée;
reprise du même appel Modal; annulation qui ne réapprouve pas; export complet et
figé; noms accentués/chemins avec espaces; vidéo importée avec/sans audio;
erreur visible et récupérable; isolation des projets; réouverture après arrêt.

## Sources et licences

Calliope et Storybored MIT. T8 et Motion-Context GPLv3: extensions externes
optionnelles, pas de copie dans l'application MIT. Le code H3 déjà possédé par
l'utilisateur dans NewsReels sert de base d'intégration Modal. Poids H3/FastH3:
licence indépendante avec restrictions territoriales, documentées séparément.
Accès Modal et droits d'utilisation des poids sont deux prérequis distincts.
