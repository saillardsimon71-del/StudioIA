# Sources et licences

Le logiciel reprend Calliope sous licence MIT. Son LICENSE original est conservé.
Base: https://github.com/benjiyaya/Calliope,
révision caea320acd6954b51eedba7a123da3d85757f0a1, version 1.5.9.
SQLite, modèles de projets/scènes/plans, file de travaux, client LLM, serveur
de médias et interface avancée proviennent de cette base.

Le parcours /studio et les tables de prises, sélections, approbations et exports
sont ajoutés dans cette branche. Le workflow FastH3 reprend le chemin ComfyUI et
Modal déjà déployé dans NewsReels sur le compte de l'opérateur, référence
a3cda9f409315dceb400c9f27bcf66349c809ed7. Aucune spécialisation éditoriale de ce
projet n'est reprise.

ComfyUI est installé depuis https://github.com/Comfy-Org/ComfyUI dans le conteneur
GPU, révision d49e888586dd8ae012c0667b33466b815fee07f7. Ses nodes H3 natifs sont
appelés par un graphe API; son code GPL n'est pas copié dans l'application MIT.
Les sources et licences de ce composant restent accessibles dans son dépôt.

Projets étudiés, dont aucun code n'est incorporé:

- https://github.com/gotejas/storybored, MIT,
  b368140dec0ccb43276b375afa887b2688027b0c: galerie de prises et parcours de choix.
- https://github.com/T8mars/comfyui-minimax-h3-audio-T8, GPL-3.0-or-later,
  1048066cbba7cb517673aade65e7ef9508269acb: H3 audio, références et compatibilité
  des poids. Les workflows Ref2VA complets requièrent d'autres poids.
- https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context, GPL-3.0,
  5335715abe54c1a9bfbe3494da29aae3e8635ce3: continuité audio/mouvement par latents.
  Cette continuité n'est pas implémentée dans la V1; seule la dernière image
  sélectionnée sert éventuellement de référence.

Les poids ne sont ni téléchargés ni redistribués par ce dépôt. Il utilise le
volume existant de l'opérateur et les quatre noms exacts définis dans h3_graph.py.
FastH3 V2 relève de la licence MiniMax H3 Community:
https://huggingface.co/FastVideo/FastVideo-FastH3-8-Step-V2.
La licence officielle et ses territoires sont à vérifier avant utilisation:
https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE.
Le logiciel ne donne aucun droit supplémentaire sur ces modèles ou leurs sorties.
