# LTX-2.5 dans StudioAI

Activation vérifiée le 13 octobre 2026.

- Modèle vidéo : LTX-2.5 Distilled, transformer INT8 ConvRot, VAE vidéo Conv BF16.
- Application Modal : `studio-ia-ltx`, fonction `render_clip`, compte `falconapp-cloud`.
- Volume des modèles : `studio-ltx25-models`. Les cinq fichiers ont été contrôlés par taille et SHA-256 pendant leur téléchargement.
- GPU : L40S, 8 CPU, 64 Gio de RAM. Aucun conteneur permanent ; arrêt après 60 secondes d'inactivité.
- Texte et images : services Agnes existants.

## Utilisation

Créer ou préparer les épisodes normalement. Le choix LTX est enregistré dans Services. StudioAI adapte le prompt et la grille de frames au modèle. Les nouvelles prises conservent le modèle choisi dans leur requête. Les fichiers des anciennes prises restent disponibles.

L'accès API du compte Modal a été autorisé et enregistré dans son profil local, pour 90 jours. Aucun jeton n'est présent dans le code ou ce document. Les poids déjà téléchargés sont montés en lecture seule ; le worker n'a pas besoin du secret Hugging Face.

## Image sans sous-titres

Les sous-titres du premier test étaient générés dans les pixels par LTX. Aucun module de sous-titrage n'était utilisé.

Activer la consigne négative avec CFG vidéo 1,5 et audio 1 ne les a pas supprimés. La modification du prompt positif a ensuite donné une image propre : décrire uniquement la scène physique, avec des paroles qui existent seulement dans le son, plutôt que mentionner les sous-titres dans une interdiction.

La compilation LTX utilise cette formulation et conserve les répliques entre guillemets, avec leur locuteur et leur langue. Elle n'envoie pas les balises propres à H3.

Deux rendus successifs avec cette formulation n'ont montré aucun sous-titre sur les images contrôlées toutes les 0,5 seconde. Ce constat ne garantit pas chaque futur tirage. Le cadre est encore susceptible de bouger malgré une consigne de caméra fixe.

## Vérification

- 197 tests ciblés réussis, 1 ignoré ; tests d'intégration supplémentaires réussis.
- Interface compilée, sans erreur de vérification Svelte ; 11 avertissements préexistants.
- Appel réel du worker par le moteur StudioAI : 768 x 1344, 137 frames à 24 fps, H.264 et AAC, 5,708 secondes.
- Génération : 197,246 secondes, hors attente d'allocation. Un seul tirage, donc pas une mesure fiable de vitesse moyenne.
- Décodage du MP4 entier réussi. Transcription : "Il dit qu'il est épuisé. Ça fait huit heures qu'il fout rien."
- Paramètres actifs et disponibilité texte/image/vidéo/montage vérifiés après relance du studio.

## Qualification à deux voix du 13 octobre 2026

Le réglage actif est désormais CFG vidéo 1 et audio 1. Même image, même prompt, même graine, deux locuteurs français, 145 frames: 230,39 s pour CFG 1,5 avec chargement initial; 78,17 s pour CFG 1 dans le même conteneur déjà chargé. Un appel du moteur StudioAI déployé dans un nouveau conteneur a ensuite pris 151,25 s. Ces essais uniques ne permettent pas d'attribuer tout l'écart au CFG.

Les trois fichiers se décodent entièrement. Whisper small retrouve les trois répliques. Les images contrôlées toutes les 0,5 seconde montrent les personnages et le décor conservés, sans sous-titres visibles. Cela ne certifie pas la stabilité vocale entre plusieurs prises, ni le naturel de la diction. La caméra peut encore dériver légèrement.

La compilation conserve le cadrage, s'appuie sur l'image pour le style en I2V et décrit la voix une fois par locuteur. Les références de la série 25 ont été corrigées et restent versionnées. Une identité visuelle approuvée est prioritaire pour les nouveaux rendus; les anciennes demandes enregistrées ne sont pas modifiées. Le canon des lieux décrit un décor inoccupé, la composition du plan porte les acteurs et leurs positions.

152 tests ciblés réussis, 1 ignoré. Les pilotes et diagnostics sont conservés dans le dossier de mission `outputs/ltx-recovery-20261013`.

## Sources

- [Guide officiel de prompts](https://docs.ltx.io/open-source-model/usage-guides/prompting-guide) : prose visuelle et paroles citées.
- [Génération officielle en deux étapes](https://docs.ltx.io/open-source-model/usage-guides/two-stage-generation/) : modèle distillé, 8 étapes puis raffinement ; augmenter le CFG ne constitue pas une amélioration générale de qualité.
- [Modèle LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5), révision `49b093a39c19169a8a81721dcc541e07e945232f`.
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI/tree/d49e888586dd8ae012c0667b33466b815fee07f7), révision utilisée dans les essais et le worker. PyTorch 2.8.0 CUDA 12.8 conservé pendant l'installation.
