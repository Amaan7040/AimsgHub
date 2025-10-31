import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
from langchain_core.embeddings import Embeddings
from config import BATCH_SIZE
import logging

logger = logging.getLogger(__name__)

class E5Embeddings(Embeddings):
    def __init__(self, model_name="intfloat/e5-large-v2", device=None):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = self.model.to(self.device)
        self.instruction = "Given a sentence, retrieve semantically similar sentences: "

    def _last_token_pooling(self, hidden_states, attention_mask):
        last_non_padded_idx = attention_mask.sum(dim=1) - 1
        batch_indices = torch.arange(hidden_states.size(0), device=self.device)
        return hidden_states[batch_indices, last_non_padded_idx]

    def embed_documents(self, texts):
        all_embeddings = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch_texts = texts[i:i+BATCH_SIZE]
            texts_with_instruction = [self.instruction + t for t in batch_texts]
            inputs = self.tokenizer(
                texts_with_instruction, padding=True, truncation=True,
                max_length=512, return_tensors="pt"
            ).to(self.device)
            with torch.no_grad():
                outputs = self.model(**inputs)
            embeddings = self._last_token_pooling(
                outputs.last_hidden_state, inputs['attention_mask']
            )
            embeddings = F.normalize(embeddings, p=2, dim=1)
            all_embeddings.extend(embeddings.cpu().numpy().tolist())
        return all_embeddings

    def embed_query(self, text):
        return self.embed_documents([text])[0]

# Global embedding model instance
embedding_model = E5Embeddings()