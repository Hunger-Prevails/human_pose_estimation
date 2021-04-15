import argparse

parser = argparse.ArgumentParser(description='Parser for all the training options')

# bool options
parser.add_argument('-shuffle', action='store_true', help='Reshuffle data at each epoch')
parser.add_argument('-half_acc', action='store_true', help='whether to use float16 for speed-up')
parser.add_argument('-save_record', action='store_true', help='Path to save train record')
parser.add_argument('-test_only', action='store_true', help='only performs test')
parser.add_argument('-val_only', action='store_true', help='only performs validation')
parser.add_argument('-pretrain', action='store_true', help='whether to load an imagenet pre-train')
parser.add_argument('-depth_host', action='store_true', help='whether to fill the depth branch with weights from a depth-only pre-train')
parser.add_argument('-resume', action='store_true', help='whether to continue from a previous checkpoint')
parser.add_argument('-extra_channel', action='store_true', help='whether to append an extra channel that masks the bbox')
parser.add_argument('-joint_space', action='store_true', help='whether to allow joint-space train data')
parser.add_argument('-do_track', action='store_true', help='whether to track cam coords via least square optim')
parser.add_argument('-do_fusion', action='store_true', help='whether to accept both color and depth input')
parser.add_argument('-do_teach', action='store_true', help='whether to force a student to mimic its teacher')
parser.add_argument('-semi_teach', action='store_true', help='whether to force a student to mimic its teacher on additional unlabelled image pairs')
parser.add_argument('-depth_only', action='store_true', help='only accepts depth input')
parser.add_argument('-nexponent', action='store_true', help='whether to feed in the negative exponent of raw depth values')
parser.add_argument('-partial_conv', action='store_true', help='whether to replace all convs in Resnet with partial convs')
parser.add_argument('-to_depth', action='store_true', help='whether to convert raw depth to actual depth')
parser.add_argument('-early_dist', action='store_true', help='whether to impose distillation loss on the third stage feature map')
parser.add_argument('-sigmoid', action='store_true', help='whether to apply sigmoid function to the feature maps before norm is taken')

# augmentation options
parser.add_argument('-geometry', action='store_true', help='whether to perform geometry augmentation')
parser.add_argument('-colour', action='store_true', help='whether to perform colour augmentation')
parser.add_argument('-eraser', action='store_true', help='whether to perform eraser augmentation')
parser.add_argument('-occluder', action='store_true', help='whether to perform occluder augmentation')

# required options
parser.add_argument('-model', required=True, help='Backbone architecture')
parser.add_argument('-model_path', help='Path to an imagenet pre-train or checkpoint')
parser.add_argument('-teacher_path', help='Path to a checkpoint of the teacher model')
parser.add_argument('-host_path', help='Path to a checkpoint of the depth-only host model')
parser.add_argument('-suffix', required=True, help='Model suffix')
parser.add_argument('-data_name', required=True, help='name of dataset')
parser.add_argument('-occ_path', help='Root path to occluders')
parser.add_argument('-save_path', required=True, help='Path to save train record')
parser.add_argument('-criterion', required=True, help='criterion function for estimation loss')

# integer options
parser.add_argument('-warmup', default=1, type=int, help='number of warmup epochs')
parser.add_argument('-n_epochs', default=20, type=int, help='number of total epochs')
parser.add_argument('-batch_size', default=64, type=int, help='Size of mini-batches for each iteration')
parser.add_argument('-n_cudas', default=2, type=int, help='Number of cuda devices available')
parser.add_argument('-workers', default=2, type=int, help='Number of subprocesses to load data')
parser.add_argument('-num_processes', default=6, type=int, help='Number of subprocesses in the process pool')
parser.add_argument('-side_in', default=257, type=int, help='side of input image')
parser.add_argument('-stride', default=16, type=int, help='stride of network for train')
parser.add_argument('-num_joints', default=19, type=int, help='number of joints in the dataset')
parser.add_argument('-depth', default=16, type=int, help='depth side of volumetric heatmap')

# train options
parser.add_argument('-warmup_factor', default=0.2, type=float, help='learn rate decay for warmup epochs')
parser.add_argument('-freeze_factor', default=0.2, type=float, help='learn rate decay for batchnorm layers')
parser.add_argument('-learn_rate', default=5e-5, type=float, help='base learn rate for train')
parser.add_argument('-grad_norm', default=5.0, type=float, help='norm for gradient clip')
parser.add_argument('-grad_scaling', default=32.0, type=float, help='magnitude of loss scaling when performing float16 computation')
parser.add_argument('-momentum', default=0.9, type=float, help='Momentum for training')
parser.add_argument('-weight_decay', default=4e-5, type=float, help='Weight decay for training')
parser.add_argument('-box_margin', default=0.6, type=float, help='factor for generating pseudo bbox from image coords')
parser.add_argument('-alpha', default=0.1, type=float, help='loss weight for alpha blend')
parser.add_argument('-alpha_warmup', default=0.1, type=float, help='warmup value for alpha under distillation setup')
parser.add_argument('-depth_range', default=100.0, type=float, help='depth range of prediction')
parser.add_argument('-random_zoom', default=0.9, type=float, help='scale for random zoom operation')
parser.add_argument('-loss_div', default=1.0, type=float, help='divisor applied to both ground-truth and estimation before loss is calculated')

# evaluation options
parser.add_argument('-thresh_solid', default=0.5, type=float, help='threshold for a solid estimation')
parser.add_argument('-thresh_close', default=2.0, type=float, help='threshold for a close estimation')
parser.add_argument('-thresh_rough', default=5.0, type=float, help='threshold for a rough estimation')

args = parser.parse_args()
