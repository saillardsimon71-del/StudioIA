# Reprise : diagnostic de l'episode 2 refuse

## Mission

Diagnostiquer les defauts reels de l'episode 2 V2 de "En prison", expliquer leurs causes et corriger le fonctionnement reutilisable de Studio IA. Ne pas se limiter a retoucher ce film. La qualite audiovisuelle de la V2 est refusee par l'utilisateur ; un statut `done`, un MP4 assemble ou des tests logiciels reussis ne constituent pas une validation qualitative.

Retour utilisateur : mauvais raccords, voix differentes de celles de l'episode 1 valide et publie, qualite globale tres insuffisante, V2 jugee pire que V1. Les passages et causes restent a etablir en examinant les vrais medias. Ne pas inventer de constats d'ecoute ou de visionnage.

## Depot et installation locale

- GitHub : https://github.com/saillardsimon71-del/StudioIA
- Branche de reprise : `codex/studio-director-manual` (utiliser la version publiee, pas la branche par defaut).
- Depot Windows : `C:/Users/saill/Documents/Codex/2026-10-07/files-pasted-by-the-user-mission/outputs/studio-ia`
- Application locale : http://127.0.0.1:8247
- Python existant : `.venv/Scripts/python.exe`
- Backend : `calliope-backend/src/calliope/` ; interface : `calliope-web/`.
- Base locale : `calliope-backend/data/calliope.db`.
- Configuration privee : `calliope-backend/calliope_config.json`. Ne jamais afficher ou publier ses secrets.
- Les donnees, videos, images, voix et configurations privees ne sont pas dans GitHub. Un clone seul ne permet pas leur controle audiovisuel. Demander uniquement les fichiers necessaires s'ils sont absents.
- L'utilisateur a demande DeepSeek V4 Pro pour l'ecriture dans le Studio. Verifier la selection locale sans afficher la cle. Le modele de l'extension VS Code peut etre different ; ne pas les confondre.

## References a comparer

Reference canonique publiee : `publish/episode-01-FINAL-V3/EPISODE-01-V3-social.mp4`, 43,136 s, 1080 x 1920. Copie : `calliope-backend/data/assets/43/studio/exports/episode-01-v3-external.mp4`. SHA-256 : `7ab17711fc0a4aa49842108c6f2bbfe5aa81b791077085f615371234760efda3`.

Serie 30, episode 1, projet 43, revision canonique 51. La continuite s'arrete lorsque Momo demande a Durio si les gardiens lui ont explique pourquoi il est ici. Les anciens essais d'episode 2 ne deviennent pas du canon. Ne pas utiliser une ancienne V1/V2 de l'episode 1 comme reference vocale a la place de cette V3.

Film refuse : `publish/episode-02-V2/EPISODE-02-V2.mp4`, 69 737 746 octets, duree mesuree 59,458008 s, produit le 18 octobre 2026 vers 13:56, heure de Paris. Copie : `work/publish-ep44-v2/EPISODE-02-V2.mp4`. Projet 44, episode 2.

Copie Drive du film refuse : https://drive.google.com/file/d/1nhd1eTsSKiKUp3lL-VNTg5lG4mMa3Ql2/view

Sources locales : `work/publish-ep44-v2/` avec `render_v2.py`, `tts_build.py`, `assemble_v2.py`, `lines.json`, `renders/`, `tts/`, `tts-voices/`, `audio/`, `segments/`. References vocales utilisees par ce script : `work/publish43/tts-voices/`. Verifier leur provenance par rapport au film publie.

Des copies textuelles des scripts, prompts, requetes sans images embarquees et metadonnees sont conservees dans `docs/studio/episode-02-v2-evidence/`. Ce sont des archives du fonctionnement execute, pas des outils approuves a relancer. Ne pas executer ces scripts : certains appellent des services GPU payants. Les chemins de ces copies correspondent a leur emplacement local d'origine.

## Travail attendu

1. Lire les instructions applicables, verifier branche/etat Git, puis lire `docs/studio/VERIFICATION-PUBLICATION-2026-10-18.md`, `docs/studio/CONTROLE-2026-10-18.md` et ce dossier de preuves. La verification de publication signale notamment un test de limitation des reparations qui reste en echec. `docs/studio/AUDIT-2026-10-17.md` est un historique, pas une preuve de qualite actuelle. Cibler les fichiers utiles au lieu de lire tout l'historique.
2. Comparer les vrais films et les sources. Produire un tableau avec passage horodate, attendu, observe, preuve, cause demontree ou hypothese, correctif general et controle de non-regression. Examiner chaque raccord avant/apres coupe : geographie, axe, regards, placements, identites, lunettes, plateaux et autres accessoires, etats initiaux/finals.
3. Remonter scenario -> storyboard/contrats -> references effectivement utilisees -> prompts/requetes executes -> moteur video -> voix natives/TTS -> montage/mixage -> QC -> export. Verifier si les scripts externes ont contourne le pipeline, ses validations, son budget ou ses exigences de revue. Verifier aussi les differences entre texte du rendu, texte double, timings et images.
4. Pour les voix, comparer chaque personnage a l'episode 1 V3 publie : reference effective, timbre, interpretation, vitesse, prononciation francaise et synchronisation. F0/LUFS, presence d'une piste audio ou transcription correcte ne prouvent pas l'identite vocale, le naturel ni le lip-sync. Controler mots parasites, descriptions techniques prononcees, repetitions et silences artificiels. Ne pas attribuer a cette V2 un ancien defaut "aaaa" sans l'avoir constate.
5. Expliquer pourquoi les defauts ont passe la supervision/QC. Distinguer resultat technique, contenu verifie, controle indisponible et validation humaine. Une transcription indisponible ou peu fiable reste non verifiee. Sans outil capable de juger une propriete visuelle/vocale, la signaler a verifier plutot que declarer la conformite.
6. Corriger les causes dans les modules reutilisables. Points d'entree : `studio/director.py`, `director_contracts.py`, `series.py`, `series_planner.py`, `pacing.py`, `editorial.py`, `ltx_prompt.py`, `ltx_prompt_v3.py`, `ltx_realization.py`, `production.py`, `service.py`, `tts.py`, `transcription.py`, `audio.py`, `export.py`, `engines/modal_tts.py`, `engines/modal_h3.py`, `calliope-web/src/lib/studio/DirectorWorkflow.svelte`. Examiner aussi les autres fichiers appeles si les preuves le justifient.
7. Ajouter des tests reproduisant les defaillances observees. Tester sans services payants, avec mocks, fichiers existants ou medias synthetiques locaux. Couvrir references/voix, contrats/raccords, texte/timing, refus du QC, cache/invalidation, reprise ciblee et export. Lancer les tests backend appropries et les commandes existantes `npm run check`, `npm test`, `npm run build` dans `calliope-web`. Verifier l'isolation de la configuration/base de test et bloquer les connexions externes pendant les tests.
8. Rendre l'app simple : DeepSeek reste le scenariste/realisateur et remplit seul les parametres avances utiles. L'utilisateur donne son idee, duree et ton, relit le storyboard, importe/valide les premieres images et controle les prises. Dialogues francais naturels, rythme adapte a la duree sans quota arbitraire de repliques, gestes expressifs, ambiance de prison audible sans masquer les paroles. Ne pas compenser les defauts par une reecriture manuelle de l'operateur ou par une multiplication aveugle des plans.

## Contraintes

- Preserver l'episode 1 publie, le canon, tous les projets/medias, les travaux locaux, l'architecture utile et les fonctions CTA existantes. Ne pas supprimer ni imposer une promotion sans examiner le choix explicite de la serie.
- Images uniquement generees manuellement par l'utilisateur dans ChatGPT, puis importees et validees. Pas de Codex, Pollinations ou API d'images automatique.
- Aucun appel GPU, TTS payant, generation d'image, regeneration complete ou nouvel appel texte payant pendant cet audit sans autorisation explicite. Les anciens plafonds ne renouvellent pas l'autorisation de reprises.
- Corrections ciblees et migrations prudentes, pas de reset, suppression, force-push ni reecriture generale. Aucun changement a Octopus. Ne jamais inscrire une cle dans Git, journaux ou prompts.
- Si un media ou une capacite d'analyse manque, avancer sur les preuves disponibles, marquer la limite et demander l'element precis necessaire. Ne pas pretendre avoir visionne/ecoute un fichier inaccessible.

## Livrables

Rapport `docs/studio/DIAGNOSTIC-EPISODE-02.md` avec preuves/horodatages, causes, limites et raisons du passage errone du QC ; correctifs generaux et tests reels avec resultats ; fichiers/commits modifies ; et courte scene de validation proposee avec cout avant toute nouvelle depense. Ne pas presenter une nouvelle version produite au hasard comme la resolution du probleme.
