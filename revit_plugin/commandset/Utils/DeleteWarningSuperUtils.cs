// 
//                       RevitAPI-Solutions
// Copyright (c) Duong Tran Quang (DTDucas) (baymax.contact@gmail.com)
// 
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// 
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
// 
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
//

namespace RevitMCPCommandSet.Utils;

public class DeleteWarningSuperUtils : IFailuresPreprocessor
{
    public int NumberErr;

    public FailureProcessingResult PreprocessFailures(FailuresAccessor failuresAccessor)
    {
        FailureProcessingResult failureProcessingResult;
        var failList = failuresAccessor.GetFailureMessages();
        if (failList.Count != 0)
        {
            foreach (var failure in failList)
            {
                var s = failure.GetSeverity();

                if (s == FailureSeverity.Warning)
                {
                    // 警告级别统一删除（原 if/else 两分支行为完全相同，合并为单句）
                    // Warnings are always deleted (both original branches were identical).
                    failuresAccessor.DeleteWarning(failure);
                }
                else if (s == FailureSeverity.Error)
                {
                    // Error 级别不自动 ResolveFailure（可能静默改动模型），仅计数并在下方回滚，交由用户处理
                    // Do not auto-resolve errors (that can silently alter the model); count them
                    // and roll the transaction back below so the user can handle them.
                    NumberErr += 1;
                }
            }

            // 存在未处理的 Error 时回滚，否则提交
            // Roll back when unresolved errors exist; otherwise commit.
            failureProcessingResult = NumberErr > 0
                ? FailureProcessingResult.ProceedWithRollBack
                : FailureProcessingResult.ProceedWithCommit;
        }
        else
        {
            failureProcessingResult = FailureProcessingResult.Continue;
        }

        return failureProcessingResult;
    }
}