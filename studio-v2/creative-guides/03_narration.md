# 03 — ÉCRIRE DES HISTOIRES ADDICTIVES

**Objectif :** Ne pas produire une succession de plans visuellement impressionnants sans enjeu. Une bonne vidéo raconte une histoire à laquelle on s'intéresse, même avec des fruits anthropomorphes.

**Principe :** Intéressant > impressionnant. Le spectateur doit avoir une raison de se soucier de ce qui arrive au personnage.

## 1. Personnages

- **Désir concret :** « récupérer sa place à la cantine » plutôt que « être heureux », si le sujet s'y prête.
- **Faille visible :** orgueil, naïveté, peur, impulsivité. Durian n'est pas seulement musclé : son orgueil peut provoquer ses problèmes.
- **Capacité d'agir :** les personnages prennent des décisions, même petites. Éviter qu'ils subissent passivement tout l'épisode.

## 2. Dialogues

- Français parlé, naturel, précis, court. Généralement une ou deux phrases brèves par prise selon sa durée.
- Éviter l'exposition artificielle : au lieu de « Comme tu le sais, le Pacte nous lie depuis cent ans », préférer une phrase qui fait avancer le conflit, comme « Tu sais ce qu'il te coûte ».
- Chaque réplique révèle une intention, crée une tension, change une relation ou apporte une information indispensable. Ne jamais répéter simplement ce qui est déjà visible.
- Différencier les voix et les personnalités : ne pas donner à tous les personnages le même registre d'écriture.

### Faire jouer l'intention

Pour chaque réplique, comprendre sans l'expliquer au spectateur : à qui le locuteur parle, ce qu'il veut obtenir maintenant, ce qu'il ressent ou cache, ce que chaque interlocuteur veut ou retient, et ce qui change entre eux. Traduire cela dans `action` par un comportement jouable — regard, posture, mains, souffle, hésitation — et, si l'autre est présent, une réaction concrète. Le geste peut contredire les mots. Une émotion nommée ne remplace pas un jeu observable.

Faire de `initial_state` l'image exacte avant le premier changement ; laisser `action` dérouler les gestes et les répliques dans un ordre réalisable ; finir sur le vrai `final_state`. Garder une place pour les silences et les réactions dans la durée. `camera_setup` fixe le cadrage de départ, repris par l'image initiale. Un mouvement de caméra ne sert la scène que s'il révèle quelque chose ou accompagne un changement d'attention ou de relation ; une caméra immobile peut être plus juste.

La coupe change le point de vue, pas les faits. Conserver les positions et l'état des accessoires d'un plan à l'autre, même avec une nouvelle image : quelqu'un encore dans un bol doit en sortir avant d'être placé derrière, un personnage qui perd l'équilibre doit se rétablir avant de préparer un saut. L'état initial ne raconte ni la suite du geste ni la chute du gag. Ces exemples décrivent une continuité à respecter, pas des actions à imposer aux histoires.

**Contre-exemples (des contrastes, pas des recettes à recopier) :**
- Faible : « Je vais très bien. » Elle a l'air triste. Mieux : elle recouvre la lettre humide d'une main, force un sourire, puis regarde la pluie ; son ami cesse d'avancer la main. Les mots n'expliquent pas l'image, ils la contredisent.
- Faible : « Je peux y aller seul. » Il marche vers la sortie. Mieux : il tend la clé d'une main tremblante ; son amie saisit sa manche plutôt que la clé, et il se retourne. Un lent rapprochement peut servir ce retournement, mais n'est pas obligatoire.
- Faible : « J'ai rien touché. » Les deux personnages se regardent. Mieux, en comédie : la caméra suit le regard de l'autre vers le sucre sur la joue ; elle l'essuie et l'étale davantage. Ici, un petit panoramique vaut mieux qu'un gros plan dramatique automatique.

## 3. Suspense et cliffhanger

- Le suspense correspond à une question vivante : « Va-t-elle signer ? » « Qui est dans l'ombre ? »
- Le cliffhanger ouvre une nouvelle direction après une progression réelle. Il donne envie de revenir, pas le sentiment d'avoir été privé de la scène promise.
- Faible : écran noir avant que l'action commence.
- Plus fort : « Il te reste sept jours. » Le spectateur comprend une nouvelle règle et anticipe la suite.

## Anti-uniformisation

Une comédie, un drame et une histoire horrifique utilisent ces principes de façon différente. Ne jamais imposer une timeline type (« apparition à 10 secondes, transformation à 25 secondes »), une morale, un méchant ou un cliffhanger systématiquement identique.

**Test final (dans le même appel de génération) :** Si l'on racontait l'épisode oralement en quinze secondes, l'enjeu et la suite seraient-ils intrigants ? Améliorer le scénario avant de répondre si nécessaire, sans lancer d'autre appel API ni de boucle de validation.
