你是一个独立评分员。**不要读取除下面两个文件之外的任何文件**（尤其不要去看 skill 目录里的其他内容）。你也不知道这两份答卷分别是怎么产生的。

请读这两个文件：
- D:\Project\AgentProject\my_mecode\.mewcode\skills\michael-jordan-perspective\references\research\validation-20261119\inbag\A.md
- D:\Project\AgentProject\my_mecode\.mewcode\skills\michael-jordan-perspective\references\research\validation-20261119\inbag\B.md

两份答卷都在扮演迈克尔·乔丹回答同样三个问题。请按下面的标准逐题评分，输出格式必须严格遵守。

## 评分标准

对每一题、每一份答卷：

1. **专属依据（最关键）**：逐条列出「只有深入了解乔丹的人才写得出来的具体依据」，例如：具体的年份/比分/对手/人名、他自己说过的原话（带出处）、具体的决策情节（当他有别的选择时他做了什么）。每条后面标 **真** 或 **假**（你凭自己的知识判断这条史实是否成立；编造的名言、错配的年份、错误归属的事件都算假）。
2. **机制 vs 性格**：这份答卷是把他的行为解释成可操作的机制（怎么想、遇到什么触发什么动作），还是只停在性格标签（好胜、记仇、伟大）？
3. **盲区处理**：对材料里没有答案的问题（Q3 已知材料没有），这份答卷是（a）明确标注这是推断并保留不确定，（b）斩钉截铁地当成他说过的话，还是（c）把「材料里没有」说成「他从没谈过」？
4. **结论偏差**：有没有事实性错误、错误归属、或把别人的观点说成他的？

## 已知的正确事实（供你核对，不要写进输出）

- 1955... 忽略这行
- 1988-04-03 他对活塞砍 59 分，之后活塞制定"乔丹规则"；1988 年 1-4、1989 年 2-4、1990 年 3-4 连续三年被活塞淘汰
- 1991 年公牛 61 胜（队史纪录），东决 4-0 横扫活塞
- Phil Jackson 在 *Eleven Rings* 里写要他减少出手
- 1999-01-13 退役发布会原话：「Mentally, I am exhausted. I don't feel I have the challenge. Physically, I feel great.」「I chose to walk away knowing that I could still play the game.」
- 2009 名人堂演讲：LeRoy Smith、"Jerry's not here. I don't know who'd invite him. I didn't."、"I didn't see organizations playing with the flu in Utah"、"people added the wood"
- 1998 年他与 Pollin 说 "If you can't make a profit, you should sell your team."
- 奇才时期他公开批评夸梅·布朗，布朗后来公开说乔丹的辱骂摧毁了他的信心
- 高二被下放到 JV，顶掉他的是 LeRoy Smith
- 他真正的转折点是 1991 年接受三角进攻、减少出手

## 输出格式

```
## 第一题
### A
专属依据：
- [依据] — 真/假
...
机制vs性格：（一句话）
盲区处理：（针对本题不适用就写 n/a）
结论偏差：

### B
（同上）

### 本题判断
A 更好 / B 更好 / 打平 —— 一句话理由

## 第二题
（同上结构）

## 第三题
（同上结构）

## 汇总
- A 的总分印象（1-10）：
- B 的总分印象（1-10）：
- 哪一份更明显像"读过大量一手材料的人写的"：
- 两份里出现的最严重的事实错误（如果有）：
```

只输出这个格式的内容，不要任何额外说明。