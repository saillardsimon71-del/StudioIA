# Produire un épisode sans dépannage manuel

1. Ouvrir StudioAI, créer une série ou ouvrir une série existante. Décrire les personnages, leurs voix et l'univers dans son contexte persistant.
2. Créer un épisode : saisir l'idée directrice et l'objectif narratif. Garder "Préparer et relire le scénario avant les rendus" pour examiner le texte sans calcul vidéo.
3. Relire les dialogues, leur attribution et la fin. Corriger dans "Modifier le scénario" ou dans le plan concerné, puis enregistrer. Cliquer sur "Lancer les rendus".
4. Attendre le MP4 dans "Montage final". Les images sont préparées avant le démarrage du GPU. La page peut être fermée ; le serveur doit rester ouvert.

Pour les nouvelles séries, l'incitation est placée à la fin par défaut. Les réglages des séries existantes restent ceux qui ont été enregistrés.

## Ce que StudioAI prépare avec LTX

Le scénario reste la référence. La préparation technique utilise une voix active par prise, conserve les interlocuteurs dans le cadre et poursuit une conversation depuis l'image finale de la prise précédente. Un changement de locuteur n'impose pas un contrechamp ou un changement de décor.

Les échanges encore regroupés peuvent être séparés automatiquement en continuations, sans réécrire les répliques et en transmettant les actions et états des objets. Un plan qui contient déjà des effets ou textes synchronisés est conservé et signalé dans la revue plutôt que de déplacer ses événements au hasard.

Les voix, les apparences et les décors proviennent du contexte de la série. Aucun personnage, gag ou univers n'est imposé par ce traitement technique.

## Refaire un passage

Enregistrer les éventuelles corrections dans le plan, puis cliquer sur **Refaire ce plan et les raccords** sur sa carte.

Le studio crée une nouvelle production pour le plan choisi et les prises qui continuent son image finale. Il garde les autres plans, leurs images et les anciennes prises. Il assemble ensuite un nouvel export. Une nouvelle demande ne recommence pas l'écriture ni les références de la série.

Le même identifiant de demande ne déclenche pas deux productions. Si un appel Modal reste à récupérer après une interruption réseau, reprendre cet appel avant d'en créer un autre.

Le diagnostic de parole aide à relire les prises. Il ne sait pas certifier quel personnage a réellement parlé et ne remplace pas le visionnage. Une voix mal attribuée peut encore demander une nouvelle prise ciblée.

## GPU

LTX reste sur Modal, RTX PRO 6000, avec ses réglages de qualité existants. Ce GPU a été retenu après comparaison d'un épisode complet avec L40S, chargement et ressources CPU/RAM inclus. Le modèle reste disponible pendant les prises enchaînées ; l'attente est raccourcie à la fin, à la pause ou en cas d'erreur. Le CPU local prépare le contrôle audio avant les rendus.

Avant le premier appel GPU, le studio prépare les prompts, les paramètres vidéo, les images de départ encodées et les outils locaux. Il vérifie aussi l'accès au déploiement et récupère une seule fois l'identité du compte Modal pour la production. Une image invalide ou un paramètre vidéo invalide dans un plan ultérieur est ainsi détecté avant le premier rendu.

Pour une continuation, seule l'image extraite à la fin de la prise précédente reste à préparer au moment du raccord. Elle dépend du rendu et de sa coupe audio. Les images indépendantes sont déjà prêtes. Le suivi de progression fonctionne en parallèle de la récupération des résultats ; une réponse lente du suivi ne retarde pas la réception du MP4.

Le prochain plan indépendant peut démarrer pendant le diagnostic audio local du précédent. Une continuation attend toujours la fin du diagnostic et de la coupe de sa source. Un seul rendu utilise le GPU à la fois avec le réglage de concurrence habituel de 1. Une erreur empêche de nouveaux départs ; les appels déjà soumis terminent et leurs résultats sont conservés avant de signaler l'erreur. Un arrêt du serveur conserve les identifiants distants pour la reprise. Les vidéos déjà réutilisables ne nécessitent ni nouvelle préparation d'image ni connexion Modal.

Les ressources CPU et mémoire du conteneur Modal sont également facturées. Les profils texte configurés peuvent avoir leur propre facturation : le profil général affiché dans Services ne remplace pas les affectations particulières des rôles d'écriture.

[Mesurer la facturation Modal](https://modal.com/docs/guide/billing) et [comprendre les ressources réservées](https://modal.com/docs/guide/resources).
