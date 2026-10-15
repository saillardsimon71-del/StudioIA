# Images avec l'abonnement Codex

Dans StudioAI, ouvrir **Services**, choisir **Codex - abonnement ChatGPT** dans le générateur d'images, puis enregistrer. Codex doit être installé sur le PC et connecté avec le compte ChatGPT. Aucune clé API OpenAI n'est nécessaire.

StudioAI transmet le prompt de l'image initiale et les références ordonnées des personnages et du décor au générateur natif de Codex. Il récupère ensuite le PNG et poursuit le parcours habituel de production. Ce choix s'applique aussi à la création des références d'une nouvelle série.

La génération utilise les limites de l'abonnement Codex. L'écriture conserve son service texte configuré, et la vidéo conserve son modèle vidéo configuré.

Le worker appelle `codex exec` avec l'authentification ChatGPT existante, sans configuration personnalisée, en lecture seule. Il ne lit ni ne copie les jetons d'authentification. Les variables de clé API OpenAI sont retirées de l'environnement de ce worker. Il accepte uniquement un nouveau fichier provenant du dossier natif `generated_images`, puis le normalise comme les autres images du studio.

Les demandes enregistrent le générateur utilisé et les références. Les diagnostics du worker sont conservés dans `calliope-backend/data/codex-images`. Une annulation arrête le worker de cette image. Le délai maximal est de dix minutes. Un échec ne bascule pas automatiquement vers Agnes ou une API payante.

Pour revenir à un service API, sélectionner **Service API** dans les mêmes réglages. Les anciens paramètres restent disponibles. Une prise échouée avec un autre générateur doit faire l'objet d'une nouvelle demande après le changement.

Documentation : [génération native d'images](https://learn.chatgpt.com/docs/image-generation), [Codex dans un pipeline](https://learn.chatgpt.com/docs/non-interactive-mode).
