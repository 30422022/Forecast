# 可选时序模型参考实现

本目录新增两个**独立编写、随机初始化**的 PyTorch 基线，用于展示公开工程中的模型接口形态。它们没有接入 FastAPI 工作流，不包含训练脚本、预训练权重或业务数据。公开 API 仍使用 Mock Provider；运行这些模块不能得到可信的负荷预测。

| 模块 | 保留的核心思路 | 与原研究实现的差异 |
| --- | --- | --- |
| `PatchTSTReference` | 每个变量独立分块，共享 patch 投影与 Transformer 编码器，展平后预测；样本内归一化 | 仅保留监督预测主干；无自监督预训练、分解可选项或完整 RevIN 仿射层 |
| `FTMixerReference` | 按候选周期重排并混合相位/周期维，FFT 频域分支，学习时间和频率分支的融合权重 | 是轻量教学实现，未复现上游 DCT、注意力、Mamba 或论文实验配置 |

两者接收 `[batch, input_length, channels]` 的浮点张量，返回 `[batch, horizon, channels]` 的**点预测张量**。输入窗口必须与配置长度一致；输出未经过训练，不能作为模型精度、概率区间或电力交易建议。配置中的通道数仅限定输入形状；这两个公开参考实现不做跨变量特征交互。

先按根目录 README 安装项目，然后安装可选模型依赖：

```bash
python -m pip install -e ".[models]"
```

从仓库根目录运行一个形状检查：

```python
import torch
from gridcast_public.reference_models.patchtst import PatchTSTConfig, PatchTSTReference
from gridcast_public.reference_models.ftmixer import FTMixerConfig, FTMixerReference

history = torch.randn(2, 48, 3)
patch_model = PatchTSTReference(PatchTSTConfig(48, 12, 3, patch_length=12, stride=6))
mix_model = FTMixerReference(FTMixerConfig(48, 12, 3, periods=(12, 24)))
print(patch_model(history).shape, mix_model(history).shape)
# torch.Size([2, 12, 3]) torch.Size([2, 12, 3])
```

安装 PyTorch 后可执行 `python -m unittest discover -s tests -p test_reference_models.py -v`。测试验证张量契约、梯度与通道隔离，不构成精度评估。要在真实预测链路使用模型，仍需经过合规的数据准备、训练、滚动回测、校准、版本管理和 Provider 接入；这些环节不在公开切片中。

概念来源：[PatchTST 论文与官方仓库](https://github.com/yuqinie98/PatchTST)、[FTMixer 论文](https://arxiv.org/abs/2405.15256)及[上游仓库](https://github.com/FMLYD/FTMixer)。本目录没有复制这些仓库的代码，模型名称用于标明研究思路与比较对象，不表示与官方实现功能等价。
