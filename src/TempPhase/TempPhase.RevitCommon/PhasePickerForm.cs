using System.Collections.Generic;
using System.Windows.Forms;

namespace EasyBIM.TempPhase
{
    internal sealed class PhasePickerForm : Form
    {
        private readonly ComboBox _phaseCombo;
        private readonly IList<PhaseChoice> _phases;

        public PhasePickerForm(string viewName, IList<PhaseChoice> phases, int currentPhaseId)
        {
            _phases = phases ?? new List<PhaseChoice>();

            Text = AddinInfo.DialogTitle;
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            Width = 470;
            Height = 220;

            Label infoLabel = new Label();
            infoLabel.Left = 14;
            infoLabel.Top = 14;
            infoLabel.Width = 430;
            infoLabel.Height = 44;
            infoLabel.Text = "Select Temporary Phase for view:" + System.Environment.NewLine + (viewName ?? string.Empty);
            Controls.Add(infoLabel);

            _phaseCombo = new ComboBox();
            _phaseCombo.Left = 14;
            _phaseCombo.Top = 72;
            _phaseCombo.Width = 430;
            _phaseCombo.DropDownStyle = ComboBoxStyle.DropDownList;
            Controls.Add(_phaseCombo);

            int selectedIndex = 0;
            for (int i = 0; i < _phases.Count; i++)
            {
                PhaseChoice choice = _phases[i];
                _phaseCombo.Items.Add(choice.PhaseName + " (" + choice.PhaseId + ")");
                if (choice.PhaseId == currentPhaseId)
                {
                    selectedIndex = i;
                }
            }

            if (_phaseCombo.Items.Count > 0)
            {
                _phaseCombo.SelectedIndex = selectedIndex;
            }

            Button okButton = new Button();
            okButton.Text = "OK";
            okButton.DialogResult = DialogResult.OK;
            okButton.Left = 270;
            okButton.Top = 128;
            okButton.Width = 84;
            Controls.Add(okButton);

            Button cancelButton = new Button();
            cancelButton.Text = "Cancel";
            cancelButton.DialogResult = DialogResult.Cancel;
            cancelButton.Left = 360;
            cancelButton.Top = 128;
            cancelButton.Width = 84;
            Controls.Add(cancelButton);

            AcceptButton = okButton;
            CancelButton = cancelButton;
        }

        public int? GetSelectedPhaseId()
        {
            if (_phaseCombo.SelectedIndex < 0 || _phaseCombo.SelectedIndex >= _phases.Count)
            {
                return null;
            }

            return _phases[_phaseCombo.SelectedIndex].PhaseId;
        }
    }
}
