import sys
sys.path.append('/project/biocomplexity/wyr6fx(Nibir)/IrrigationMapping/KIIM')

import os
import hydra
from omegaconf import DictConfig, OmegaConf
import pytorch_lightning as pl
from pytorch_lightning.callbacks import ModelCheckpoint, EarlyStopping
from pytorch_lightning.strategies import DDPStrategy
from pathlib import Path
import time, yaml
import torch.nn as nn
import wandb
import pandas as pd
from data.data_module import IrrigationDataModule
from models_v2.TeacherStudentModel import TeacherStudentModel
# from models.TeacherStudentModel_v1 import TeacherStudentKIIM
from utils.train_config import save_experiment_config
import torch
torch.set_float32_matmul_precision('medium')

@hydra.main(config_path="/project/biocomplexity/wyr6fx(Nibir)/IrrigationMapping/KIIM/config", config_name="Teacher-Student-Training_v1", version_base="1.2")
def train(cfg: DictConfig) -> None:
    print(f"Running on GPUs: {cfg.train.devices}")
    pl.seed_everything(cfg.train.seed)

    data_module = IrrigationDataModule(cfg, merge_train_valid=False)
    data_module.setup('fit')
    data_module.setup('test')
    
#     print(data_module.train_dataloader().batch_size, len(data_module.train_dataloader()))
    
    
# #     print(data_module.train_dataset)
    # print(**cfg)
    # student_model = KIIM(**cfg.model)
    # print(student_model)
#     teacher_model = KIIM(**cfg.model)
    
#     # checkpoint = torch.load("/project/biocomplexity/wyr6fx(Nibir)/IrrigationMapping/AZ/result_stats/checkpoints/epoch=45-val_iou_macro_irr=0.734.ckpt")
#     # teacher_model.load_state_dict(checkpoint['state_dict'], strict=False)

#     # teacher_model = TeacherStudentKIIM.load_from_checkpoint("/project/biocomplexity/wyr6fx(Nibir)/IrrigationMapping/WA/Supervised/20/result_stats/checkpoints/epoch=13-val_iou_macro_irr=0.594.ckpt", strict=False, **cfg.model)
# #     # student_model = KIIM(...)
#     # model = TeacherStudentKIIM(teacher=teacher_model.student, student=teacher_model.student, num_classes=cfg.model.num_classes, alpha=cfg.train.alpha,alpha_decay=cfg.train.alpha_decay)
    
    model = TeacherStudentModel(**cfg)
    
    
    print(model)


    if len(cfg.train.devices) > 1:
        model = nn.SyncBatchNorm.convert_sync_batchnorm(model)

    save_dir = Path(cfg.logging.run_name)
    save_dir.mkdir(parents=True, exist_ok=True)

    callbacks = [
        ModelCheckpoint(
            dirpath=save_dir / "checkpoints",
            filename="{epoch}-{val_iou_macro_irr:.3f}",
            monitor=cfg.train.monitor,
            mode="max",
            save_top_k=1,
            save_last=True,
        )
    ]

    if cfg.train.early_stopping:
        callbacks.append(
            EarlyStopping(
                monitor=cfg.train.monitor,
                mode="max",
                patience=cfg.train.patience,
                verbose=True
            )
        )

    strategy = DDPStrategy(find_unused_parameters=False) if len(cfg.train.devices) > 1 else 'ddp'

    trainer = pl.Trainer(
        max_epochs=cfg.train.max_epochs,
        accelerator=cfg.train.accelerator,
        devices=cfg.train.devices,
        strategy=strategy,
        callbacks=callbacks,
        gradient_clip_val=cfg.train.gradient_clip_val,
        accumulate_grad_batches=cfg.train.accumulate_grad_batches,
        check_val_every_n_epoch=cfg.train.check_val_every_n_epoch,
        precision=cfg.train.precision,
        deterministic=cfg.train.get('deterministic', False),
        log_every_n_steps=50,
        enable_progress_bar=True
        
    )

    try:
        trainer.fit(model, data_module)

        best_model_path = callbacks[0].best_model_path
        print(f"Best model saved at: {best_model_path}")
        with open(save_dir / "best_model_path.txt", "w") as f:
            f.write(best_model_path)

        print("\nRunning validation and test...\n")

        val_results = trainer.validate(model, datamodule=data_module)
        val_df = pd.DataFrame(val_results)
        val_df.to_csv(save_dir / "validation_results.csv", index=False)

        test_results = trainer.test(model, datamodule=data_module)
        test_df = pd.DataFrame(test_results)
        test_df.to_csv(save_dir / "test_results.csv", index=False)

        if cfg.train.save_model:
            trainer.save_checkpoint(save_dir / "final_model.ckpt")

    except Exception as e:
        print(f"Training failed: {str(e)}")
        import traceback
        traceback.print_exc()
        raise e

    finally:
        save_experiment_config(cfg, data_module, model, trainer, save_dir)
        if hasattr(model, 'get_metrics'):
            final_metrics = model.get_metrics()
            OmegaConf.save(config=final_metrics, f=save_dir / "final_metrics.yaml")

if __name__ == "__main__":
    train()
