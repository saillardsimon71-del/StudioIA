import asyncio
import json
from pathlib import Path

from calliope.config import settings
from calliope.studio import service

root = Path(r'AUDIT_WORKSPACE')
request = {
    'image_base_url': settings.studio_image_base_url,
    'image_model': settings.studio_image_model,
    'aspect_ratio': '9:16',
    'reference_paths': [str(root / 'work/mort-de-honte-revision2-opening-manual-2.png')],
    'prompt': "One new vertical 9:16 cinematic frame of CLARA, the strawberry woman standing on the LEFT in the supplied scene. Keep her EXACT face, strawberry head shape with green leaves and golden seeds, gold earrings, cream jacket and black dress. Keep the same detailed stylized 3D rendering, warm light and salon architecture. In this new medium close-up, Clara alone faces the viewer and energetically points her raised index finger directly toward the lens while beginning to speak. Her face and pointing hand fill the composition, from head to waist. Her expression is lively and engaged, with eyes on the viewer. The warm mirrors, shelves and plants are softly visible behind her. Only Clara is in this new frame, no other people or duplicate heads. No smartphone, graphic overlay, caption or text."
}
artifact = root / 'work/mort-de-honte-revision2-midroll-manual.png'
if not artifact.exists():
    (root / 'work/mort-de-honte-revision2-midroll-request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    content, metadata = asyncio.run(service._generate_image(request))
    artifact.write_bytes(content)
    (root / 'work/mort-de-honte-revision2-midroll-metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'image_path': str(artifact)}, ensure_ascii=False))
