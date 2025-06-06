import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import torch
from datasets import load_from_disk
import pickle
from transformers import AutoTokenizer, AutoModel
from collections import Counter
from torch import Tensor
from sklearn.metrics.pairwise import cosine_similarity
from collections import Counter, defaultdict

# Model and Tokenizer Setup
model_name = "Salesforce/SFR-Embedding-Mistral"  # Change if needed
cache_dir = "/scratch/project_2000539/maryam/embed/.cache"
tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
model = AutoModel.from_pretrained(model_name, cache_dir=cache_dir, torch_dtype=torch.float16).cuda()


# Load Dataset
dataset_original = load_from_disk("/scratch/project_2000539/maryam/mtop_domain/de")
dataset = list(dataset_original)

# Count topic occurrences
topic_counts = Counter(doc["label_text"] for doc in dataset)

# Sort dataset by most repeated topics
dataset.sort(key=lambda x: topic_counts[x["label_text"]], reverse=True)

# Keep only up to 120 docs per topic
max_per_topic = 120
topic_seen = defaultdict(int)
dataset = [doc for doc in dataset if topic_seen[doc["label_text"]] <
           max_per_topic and not topic_seen.__setitem__(doc["label_text"], topic_seen[doc["label_text"]] + 1)]

def last_token_pool(last_hidden_states: Tensor,
                 attention_mask: Tensor) -> Tensor:
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    else:
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]

# Function to compute embeddings
def get_embeddings(text):
    """Extracts embeddings from model."""
    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512).to("cuda")
    with torch.no_grad():
        outputs = model(**inputs)
    return outputs.last_hidden_state, inputs.attention_mask  # (batch_size, seq_length, hidden_size)

# Process Documents
num_heads = 32  # Define the number of attention heads
total_docs = 960  # Total documents

cosine_scores = []
output_path_base = "data/mtop_domain/de/"

file_counts = 0
for doc in dataset[:total_docs]:  # Assuming dataset is loaded from JSONL

    title = doc["label_text"]  # Using text as the title since JSONL does not have a separate title field
    file_counts += 1

    # Get Title Embeddings (Mean Pooling)
    title_hidden, title_mask = get_embeddings(title)
    title_embedding = last_token_pool(title_hidden, title_mask ).cpu().numpy()

    n_samples = title_embedding.shape[0]
    head_size = title_embedding.shape[1] // num_heads

    title_embedding = title_embedding.reshape(num_heads, head_size).mean(axis=0, keepdims=True)  # Shape: (1, 128)


    id = str(doc['id'])
    output_path = output_path_base + id + ".pkl"

    all_embd = []

    with open(output_path, "rb") as f:
        while True:
            try:
                # Load one batch at a time
                padded_embeddings = pickle.load(f)
                
                # Apply mean pooling to the batch
                all_embd.append(padded_embeddings)
            
            except EOFError:
                # End of file reached
                break

    all_embd = np.concatenate(all_embd, axis=0)

    n_samples = all_embd.shape[0]
    head_size = all_embd.shape[1] // num_heads
    split_embeddings = all_embd.reshape(n_samples, num_heads, head_size)

    flat_embeddings = split_embeddings.reshape(n_samples * num_heads, head_size)
    doc_embedding = flat_embeddings.mean(axis=0, keepdims=True)

    flat_embeddings = np.concatenate([doc_embedding, flat_embeddings], axis=0)
    # Compute Cosine Similarity (Title vs. Each Head)
    similarities = cosine_similarity(title_embedding, flat_embeddings)[0]  # Shape: (n_samples * num_heads,)

    # Inside the loop instead of appending full similarities directly:
    doc_similarity = similarities[0]
    head_similarities = similarities[1:]

    cosine_scores.append(similarities)

# Convert to NumPy Array for Visualization
cosine_scores = np.array(cosine_scores).T  # Shape: (num_heads, num_docs)

# Normalize each head's similarities to [0, 1]
normalized_scores = []

for head_scores in cosine_scores:
    min_val = np.min(head_scores)
    max_val = np.max(head_scores)
    norm = (head_scores - min_val) / (max_val - min_val + 1e-9)  # Avoid division by zero
    normalized_scores.append(norm)

# Count how many docs per topic (in order of appearance)
topic_order = [doc["label_text"] for doc in dataset[:total_docs]]
topic_counts = Counter(topic_order)
topic_boundaries = []

# Preserve order and build x_labels
x_ticks = []
tick_position = 0

for topic, count in topic_counts.items():
    if count < 10:
        break
    x_ticks.append((tick_position + count // 2, f"{topic} ({count})"))
    tick_position += count
    topic_boundaries.append(tick_position)

# Plot heatmap
plt.figure(figsize=(20, 10)) # cmap="Reds"
sns.heatmap(cosine_scores, cmap="coolwarm", yticklabels=[f"Head {i}" for i in range(num_heads + 1)])

# Custom x-ticks in the middle of each topic group
positions, labels = zip(*x_ticks)
plt.xticks(positions, labels, rotation=90, fontsize=8)

# Draw vertical lines at topic boundaries
for boundary in topic_boundaries[:-1]:  # skip last one to avoid line at far right edge
    plt.axvline(x=boundary, color='black', linestyle='--', linewidth=0.5)

plt.xlabel("Documents (Grouped by Category)")
plt.ylabel("Vector Embedding Heads")
plt.title("Heatmap of Cosine Similarity Between Title and Text Attention Heads")

# Show plot
plt.savefig("mtop_heatmap_topics.png", dpi=300, bbox_inches="tight")
plt.show()
plt.close()