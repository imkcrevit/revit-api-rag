using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using RevitMCPSDK.API.Interfaces;

namespace RevitMCPCommandSet.Services
{
    public class DeleteElementEventHandler : IExternalEventHandler, IWaitableExternalEventHandler
    {
        // 执行结果
        public bool IsSuccess { get; private set; }

        // 成功删除的元素数量
        public int DeletedCount { get; private set; }
        // 状态同步对象
        public bool TaskCompleted { get; private set; }
        private readonly ManualResetEvent _resetEvent = new ManualResetEvent(false);
        // 要删除的元素ID数组
        public string[] ElementIds { get; set; }
        // 实现IWaitableExternalEventHandler接口
        public bool WaitForCompletion(int timeoutMilliseconds = 10000)
        {
            _resetEvent.Reset();
        return _resetEvent.WaitOne(timeoutMilliseconds);
        }
        public void Execute(UIApplication app)
        {
            try
            {
                var doc = app.ActiveUIDocument.Document;
                DeletedCount = 0;
                if (ElementIds == null || ElementIds.Length == 0)
                {
                    IsSuccess = false;
                    return;
                }
                // 创建待删除元素ID集合
                List<ElementId> elementIdsToDelete = new List<ElementId>();
                List<string> invalidIds = new List<string>();
                foreach (var idStr in ElementIds)
                {
                    if (int.TryParse(idStr, out int elementIdValue))
                    {
                        var elementId = new ElementId(elementIdValue);
                        // 检查元素是否存在
                        if (doc.GetElement(elementId) != null)
                        {
                            elementIdsToDelete.Add(elementId);
                        }
                    }
                    else
                    {
                        invalidIds.Add(idStr);
                    }
                }
                if (invalidIds.Count > 0)
                {
                    TaskDialog.Show("警告", $"以下ID无效或元素不存在：{string.Join(", ", invalidIds)}");
                }
                // 如果有可删除的元素，则执行删除
                if (elementIdsToDelete.Count > 0)
                {
                    // 破坏性操作前弹窗确认，展示删除数量与涉及类别
                    // Confirm before this destructive operation; show count and categories.
                    var categoryNames = new HashSet<string>();
                    foreach (var eid in elementIdsToDelete)
                    {
                        string catName = doc.GetElement(eid)?.Category?.Name;
                        if (!string.IsNullOrEmpty(catName))
                            categoryNames.Add(catName);
                    }
                    string catSummary = categoryNames.Count > 0
                        ? string.Join(", ", categoryNames)
                        : "未知类别 / unknown";

                    TaskDialogResult confirm = TaskDialog.Show(
                        "确认删除 / Confirm Delete",
                        $"即将删除 {elementIdsToDelete.Count} 个元素。\n涉及类别：{catSummary}\n此操作不可撤销，是否继续？\n\n" +
                        $"About to delete {elementIdsToDelete.Count} element(s).\nCategories: {catSummary}\nThis cannot be undone. Continue?",
                        TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No,
                        TaskDialogResult.No);

                    if (confirm != TaskDialogResult.Yes)
                    {
                        IsSuccess = false;
                        return;
                    }

                    using (var transaction = new Transaction(doc, "Delete Elements"))
                    {
                        try
                        {
                            transaction.Start();

                            // 批量删除元素
                            ICollection<ElementId> deletedIds = doc.Delete(elementIdsToDelete);
                            DeletedCount = deletedIds.Count;

                            transaction.Commit();
                        }
                        catch
                        {
                            // 异常时显式回滚，避免事务处于未结束状态
                            // Roll back explicitly on error so the transaction is not left open.
                            if (transaction.HasStarted() && !transaction.HasEnded())
                                transaction.RollBack();
                            throw;
                        }
                    }
                    IsSuccess = true;
                }
                else
                {
                    TaskDialog.Show("错误", "没有有效的元素可以删除");
                    IsSuccess = false;
                }
            }
            catch (Exception ex)
            {
                TaskDialog.Show("错误", "删除元素失败: " + ex.Message);
                IsSuccess = false;
            }
            finally
            {
                TaskCompleted = true;
                _resetEvent.Set();
            }
        }
        public string GetName()
        {
            return "删除元素";
        }
    }
}
