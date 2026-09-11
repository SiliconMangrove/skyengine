"""
转换AGV路由数据到PyTorch格式
Converts routing data collected from agv_routing_data_collector to PyTorch DataLoader format

适用于training duel_solver (AGV routing time predictor)
"""

import json
import torch
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from torch.utils.data import Dataset, DataLoader


class RoutingDataset(Dataset):
    """PyTorch Dataset for AGV routing data"""
    
    def __init__(
        self,
        data_files: List[Path],
        map_size: int = 100,
        max_jobs: int = 20,
        transform=None
    ):
        """
        Args:
            data_files: List of JSONL files from agv_routing_data_collector
            map_size: Grid size (assume square maps)
            max_jobs: Max concurrent jobs (pad/truncate as needed)
            transform: Optional transformation function
        """
        self.map_size = map_size
        self.max_jobs = max_jobs
        self.transform = transform
        self.samples = []
        
        # Load all samples from files
        for file_path in data_files:
            self._load_episode(file_path)
        
        print(f"Loaded {len(self.samples)} routing samples from {len(data_files)} episodes")
    
    def _load_episode(self, filepath: Path):
        """Load routing data from episode JSONL file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                episode = json.load(f)
            
            episode_id = episode.get('episode_id', 'unknown')
            seed = episode.get('seed', -1)
            
            # Process each decision as a training sample
            for decision in episode.get('decisions', []):
                sample = {
                    'episode_id': episode_id,
                    'seed': seed,
                    'timestep': decision['t'],
                    'map_state': decision.get('map_state', {}),
                    'all_jobs': decision.get('all_jobs', []),
                    'candidates': decision.get('candidates', [])
                }
                self.samples.append(sample)
        
        except Exception as e:
            print(f"Warning: Could not load {filepath}: {e}")
    
    def __len__(self):
        return len(self.samples)
    
    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        """
        Returns a training sample as tensors
        
        Outputs:
            {
                'map_grid': (1, H, W) - binary obstacle map (placeholder)
                'job_features': (Max_Jobs, job_feat_dim) - padded job queue
                'candidate_features': (N_Candidates, 5) - candidate routes
                'transport_times': (N_Candidates,) - ground truth times
                'metadata': dict with episode_id, seed, timestep
            }
        """
        sample = self.samples[idx]
        
        # 1. Reconstruct map grid from state
        map_grid = self._reconstruct_map(sample['map_state'])
        map_grid = torch.from_numpy(map_grid).unsqueeze(0).float()  # (1, H, W)
        
        # 2. Encode job features (all pending operations)
        job_features = self._encode_jobs(sample['all_jobs'])
        job_features = torch.from_numpy(job_features).float()  # (Max_Jobs, job_feat_dim)
        
        # 3. Encode candidate routes
        candidate_features, transport_times = self._encode_candidates(sample['candidates'])
        candidate_features = torch.from_numpy(candidate_features).float()
        transport_times = torch.from_numpy(transport_times).float()
        
        # 4. Metadata
        metadata = {
            'episode_id': sample['episode_id'],
            'seed': sample['seed'],
            'timestep': sample['timestep'],
            'n_candidates': len(sample['candidates'])
        }
        
        return {
            'map_grid': map_grid,
            'job_features': job_features,
            'candidate_features': candidate_features,
            'transport_times': transport_times,
            'metadata': metadata
        }
    
    def _reconstruct_map(self, map_state: Dict) -> np.ndarray:
        """Reconstruct binary obstacle grid from compressed state"""
        try:
            h = map_state.get('map_size', {}).get('h', self.map_size)
            w = map_state.get('map_size', {}).get('w', self.map_size)
            
            grid = np.zeros((h, w), dtype=np.uint8)
            
            # Restore obstacle indices
            obstacle_indices = map_state.get('obstacle_indices', [])
            for idx in obstacle_indices:
                if 0 <= idx < h * w:
                    y, x = divmod(idx, w)
                    grid[y, x] = 1
            
            return grid
        except:
            # Fallback: empty grid
            return np.zeros((self.map_size, self.map_size), dtype=np.uint8)
    
    def _encode_jobs(self, jobs_list: List[Dict]) -> np.ndarray:
        """Encode all jobs into fixed-size feature array"""
        # job_feat_dim = 6 (job_id_hash, op_id, machine_id, duration, priority, queue_pos)
        job_feat_dim = 6
        features = np.zeros((self.max_jobs, job_feat_dim), dtype=np.float32)
        
        for idx, job in enumerate(jobs_list[:self.max_jobs]):
            features[idx, 0] = hash(job.get('job_id', '')) % 1000  # hash job_id
            features[idx, 1] = int(job.get('op_id', 0))
            features[idx, 2] = int(job.get('machine', -1))
            features[idx, 3] = float(job.get('duration', 0)) / 100.0  # normalize
            features[idx, 4] = 1.0 if job.get('status', '') == 'ready' else 0.5
            features[idx, 5] = idx / self.max_jobs  # position in queue
        
        return features
    
    def _encode_candidates(self, candidates: List[Dict]) -> Tuple[np.ndarray, np.ndarray]:
        """Encode candidate routes and their true transport times"""
        # candidate_feat_dim = 5 (start_x, start_y, end_x, end_y, priority)
        if not candidates:
            # Return empty arrays if no candidates
            return (
                np.zeros((1, 5), dtype=np.float32),
                np.zeros((1,), dtype=np.float32)
            )
        
        n_candidates = len(candidates)
        features = np.zeros((n_candidates, 5), dtype=np.float32)
        times = np.zeros((n_candidates,), dtype=np.float32)
        
        for i, cand in enumerate(candidates):
            start_pos = cand.get('start_pos', [0, 0])
            target_pos = cand.get('target_machine', [0, 0])
            
            features[i, 0] = float(start_pos[0])
            features[i, 1] = float(start_pos[1])
            features[i, 2] = float(target_pos[0])
            features[i, 3] = float(target_pos[1])
            features[i, 4] = float(cand.get('priority', 1.0))
            
            # Ground truth label
            times[i] = float(cand.get('true_transport_time', 1.0))
        
        return features, times


def create_routing_dataloader(
    data_dir: str = "./data/agv_routing",
    batch_size: int = 32,
    train_split: float = 0.8,
    shuffle: bool = True,
    num_workers: int = 0
) -> Tuple[DataLoader, DataLoader]:
    """
    Create train/val dataloaders for AGV routing data
    
    Args:
        data_dir: Directory containing routing JSONL files
        batch_size: Batch size for DataLoader
        train_split: Fraction for training set
        shuffle: Whether to shuffle
        num_workers: Number of parallel workers
    
    Returns:
        (train_dataloader, val_dataloader)
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        raise ValueError(f"Data directory not found: {data_dir}")
    
    # Find all JSONL files
    json_files = list(data_path.glob("routing_*.jsonl"))
    if not json_files:
        print(f"Warning: No routing files found in {data_dir}")
        json_files = []
    
    # Split into train/val
    n_train = int(len(json_files) * train_split)
    train_files = json_files[:n_train]
    val_files = json_files[n_train:]
    
    # Create datasets
    train_dataset = RoutingDataset(train_files)
    val_dataset = RoutingDataset(val_files)
    
    # Create dataloaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=True
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True
    )
    
    return train_loader, val_loader


class RoutingDataCollator:
    """Collate function for variable-sized routing data"""
    
    def __call__(self, batch: List[Dict]) -> Dict[str, torch.Tensor]:
        """
        Collate batch of variable-sized routing samples
        
        Handles:
        - Different number of candidates per sample
        - Padding to max in batch
        """
        # Stack fixed-size tensors
        map_grids = torch.stack([s['map_grid'] for s in batch])
        job_features = torch.stack([s['job_features'] for s in batch])
        
        # Handle variable-sized candidates
        # Pad to max in batch
        max_candidates = max(s['candidate_features'].shape[0] for s in batch)
        
        candidate_features_list = []
        transport_times_list = []
        masks_list = []
        
        for s in batch:
            n = s['candidate_features'].shape[0]
            padded_cands = torch.zeros(max_candidates, 5)
            padded_cands[:n] = s['candidate_features']
            candidate_features_list.append(padded_cands)
            
            padded_times = torch.zeros(max_candidates)
            padded_times[:n] = s['transport_times']
            transport_times_list.append(padded_times)
            
            # Mask for valid candidates
            mask = torch.zeros(max_candidates, dtype=torch.bool)
            mask[:n] = True
            masks_list.append(mask)
        
        candidate_features = torch.stack(candidate_features_list)
        transport_times = torch.stack(transport_times_list)
        masks = torch.stack(masks_list)
        
        return {
            'map_grid': map_grids,
            'job_features': job_features,
            'candidate_features': candidate_features,
            'transport_times': transport_times,
            'candidate_mask': masks,
            'metadata': [s['metadata'] for s in batch]
        }


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Convert AGV routing data to PyTorch format"
    )
    parser.add_argument(
        "--data_dir", type=str, default="./data/agv_routing",
        help="Directory with routing data"
    )
    parser.add_argument(
        "--batch_size", type=int, default=32,
        help="Batch size"
    )
    parser.add_argument(
        "--train_split", type=float, default=0.8,
        help="Train/val split ratio"
    )
    
    args = parser.parse_args()
    
    try:
        # Create dataloaders
        train_loader, val_loader = create_routing_dataloader(
            data_dir=args.data_dir,
            batch_size=args.batch_size,
            train_split=args.train_split
        )
        
        print(f"\n✓ Created dataloaders:")
        print(f"  - Train: {len(train_loader.dataset)} samples")
        print(f"  - Val: {len(val_loader.dataset)} samples")
        print(f"  - Batch size: {args.batch_size}")
        
        # Inspect first batch
        print("\nFirst batch shape:")
        batch = next(iter(train_loader))
        for key, val in batch.items():
            if isinstance(val, torch.Tensor):
                print(f"  {key}: {val.shape}")
            elif isinstance(val, list):
                print(f"  {key}: list of {len(val)}")
    
    except Exception as e:
        print(f"Error: {e}")
        print("No data available yet. Run agv_routing_data_collector.py first.")
