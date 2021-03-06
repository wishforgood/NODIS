
from dataloaders.visual_genome import VGDataLoader, VG
import numpy as np
import torch

from config import ModelConfig
from lib.pytorch_misc import optimistic_restore
from lib.evaluation.sg_eval import BasicSceneGraphEvaluator
from tqdm import tqdm
from config import BOX_SCALE, IM_SCALE
import dill as pkl
import os

conf = ModelConfig()
from lib.NODIS import NODIS

train, val, test = VG.splits(num_val_im=0, filter_duplicate_rels=True,
                             use_proposals=conf.use_proposals,
                             filter_non_overlap=conf.mode == 'sgdet')
if conf.test:
    val = test

train_loader, val_loader = VGDataLoader.splits(train, val, mode='rel',
                                               batch_size=conf.batch_size,
                                               num_workers=conf.num_workers,
                                               num_gpus=conf.num_gpus)

detector = NODIS(classes=train.ind_to_classes, rel_classes=train.ind_to_predicates,
                 num_gpus=conf.num_gpus, mode=conf.mode, require_overlap_det=True,
                 use_resnet=conf.use_resnet, order=conf.order,
                 use_proposals=conf.use_proposals)


detector.cuda()
ckpt = torch.load(conf.ckpt)
conf_matrix = np.zeros((3,1))
optimistic_restore(detector, ckpt['state_dict'])

all_pred_entries = []


def val_batch(batch_num, b, evaluator, thrs=(20, 50, 100)):

    det_res = detector[b]
    if conf.num_gpus == 1:
        det_res = [det_res]
    batch_cls = []
    batch_reg = []
    for i, (boxes_i, objs_i, obj_scores_i, rels_i, pred_scores_i) in enumerate(det_res):
        gt_entry = {
            'gt_classes': val.gt_classes[batch_num + i].copy(),
            'gt_relations': val.relationships[batch_num + i].copy(),
            'gt_boxes': val.gt_boxes[batch_num + i].copy(),
        }
        assert np.all(objs_i[rels_i[:,0]] > 0) and np.all(objs_i[rels_i[:,1]] > 0)
        # assert np.all(rels_i[:,2.0] > 0)

        pred_entry = {
            'pred_boxes': boxes_i * BOX_SCALE/IM_SCALE,
            'pred_classes': objs_i,
            'pred_rel_inds': rels_i,
            'obj_scores': obj_scores_i,
            'rel_scores': pred_scores_i,
        }
        all_pred_entries.append(pred_entry)

        pred_to_gt, pred_5ples, rel_scores, rt_cls, rt_reg = evaluator[conf.mode].evaluate_scene_graph_entry(
            gt_entry,
            pred_entry,
        )
        batch_cls.append(rt_cls)
        batch_reg.append(rt_reg)
    return batch_cls, batch_reg

evaluator = BasicSceneGraphEvaluator.all_modes(multiple_preds=False)#conf.multi_pred
if conf.cache is not None and os.path.exists(conf.cache):
    print("Found {}! Loading from it".format(conf.cache))
    with open(conf.cache,'rb') as f:
        all_pred_entries = pkl.load(f)
    for i, pred_entry in enumerate(tqdm(all_pred_entries)):
        gt_entry = {
            'gt_classes': val.gt_classes[i].copy(),
            'gt_relations': val.relationships[i].copy(),
            'gt_boxes': val.gt_boxes[i].copy(),
        }
        evaluator[conf.mode].evaluate_scene_graph_entry(
            gt_entry,
            pred_entry,
        )
    evaluator[conf.mode].print_stats()
else:
    detector.eval()
    rt_clses = []
    rt_regs = []
    for val_b, batch in enumerate(tqdm(val_loader)):
        rt_cls, rt_reg = val_batch(conf.num_gpus*val_b, batch, evaluator)
        rt_clses = rt_clses + rt_cls
        rt_regs = rt_regs + rt_reg

    evaluator[conf.mode].print_stats()

    if conf.cache is not None:
        with open(conf.cache,'w') as f:
            pkl.dump([rt_clses, rt_regs], f)
